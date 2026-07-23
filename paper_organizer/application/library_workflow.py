"""Manual-first collection, duplicate review and library editing workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fitz

from paper_organizer.application.analysis_queue import (
    AnalysisQueueError,
    AnalysisQueueItem,
    AnalysisQueueStore,
)
from paper_organizer.application.legacy_migration import (
    LegacyMigrationPreview,
    LegacyMigrationResult,
    LegacyMigrationService,
    LegacyMigrationTrashEntry,
)
from paper_organizer.application.summary_service import SummaryExecution
from paper_organizer.core.classifier import (
    TaxonomyError,
    classify_text,
    extract_venue,
)
from paper_organizer.core.discovery import DiscoveryTracker, iter_pdf_candidates
from paper_organizer.core.document_identity import (
    PdfIdentityError,
    build_identity_from_pages,
    compare_identities,
    extract_page_texts,
    sha256_file,
)
from paper_organizer.core.indexer import (
    SIDECAR_SUFFIX,
    iter_record_paths,
    load_record,
    rebuild_library_index,
)
from paper_organizer.core.paperpack import (
    PAPERPACK_SUFFIX,
    PaperPackError,
    build_content_payload,
    content_pages,
    extract_paperpack_pdf,
    import_pdf_to_paperpack,
    inspect_paperpack,
    iter_paperpacks,
    load_paperpack_content,
    load_paperpack_metadata,
    replace_paperpack_pdf,
    update_paperpack,
)
from paper_organizer.core.search_index import (
    SearchHit,
    SearchIndexError,
    rebuild_search_index,
    search as search_full_text,
    update_search_entry,
)
from paper_organizer.infra.settings import (
    AppSettings,
    default_settings_path,
    load_settings,
    save_settings,
)
from paper_organizer.models.paper import (
    DocumentIdentity,
    DuplicateKind,
    DuplicateMatch,
    WrapperPage,
)


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class LibraryWorkflowError(RuntimeError):
    pass


@dataclass(slots=True)
class EditablePaperMetadata:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    category: str = "Uncategorized"
    subcategory: str = "General"
    tags: list[str] = field(default_factory=list)
    summary_ko: str = ""


@dataclass(frozen=True, slots=True)
class DuplicateReference:
    match: DuplicateMatch
    title: str
    pdf_path: Path
    sidecar_path: Path
    source_variant: str

    @property
    def confirmed(self) -> bool:
        return self.match.confirmed


@dataclass(frozen=True, slots=True)
class ReviewItem:
    path: Path
    identity: DocumentIdentity
    metadata: EditablePaperMetadata
    detection_status: str
    detection_reason: str
    duplicate: DuplicateReference | None = None
    page_texts: tuple[str, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ScanProblem:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ReviewScan:
    items: tuple[ReviewItem, ...]
    pending_stability: int
    problems: tuple[ScanProblem, ...]
    auto_organized: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrganizedPaper:
    pdf_path: Path
    sidecar_path: Path
    warning: str = ""


@dataclass(frozen=True, slots=True)
class TrashOperation:
    operation_id: str
    manifest_path: Path
    trashed_path: Path


@dataclass(frozen=True, slots=True)
class TrashEntry:
    operation_id: str
    manifest_path: Path
    original_path: Path
    trashed_path: Path
    duplicate_of: Path


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    pdf_path: Path
    sidecar_path: Path
    metadata: EditablePaperMetadata
    work_id: str
    source_variant: str
    record: dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PaperPackWorkingCopy:
    paperpack_path: Path
    pdf_path: Path
    base_pdf_sha256: str
    current_pdf_sha256: str
    base_revision: int
    current_revision: int
    changed: bool
    conflicted: bool


@dataclass(frozen=True, slots=True)
class PaperPackPdfUpdate:
    paperpack_path: Path
    working_pdf_path: Path
    previous_pdf_sha256: str
    pdf_sha256: str
    revision: int
    warning: str = ""


@dataclass(frozen=True, slots=True)
class StartupSnapshot:
    library_entries: int
    local_json_files: int
    problems: tuple[str, ...] = ()


def default_input_dir() -> Path:
    return Path.home() / "Downloads"


def default_library_root() -> Path:
    return Path.home() / "Documents" / "Paper Library"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _import_receipts_path(library_root: Path) -> Path:
    return library_root / "state" / "imported-sources.json"


def _load_import_receipts(library_root: Path) -> dict[str, dict[str, Any]]:
    path = _import_receipts_path(library_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("sources"), dict):
            return {}
        return {
            str(key): value
            for key, value in data["sources"].items()
            if isinstance(value, dict)
        }
    except (OSError, TypeError, json.JSONDecodeError):
        return {}


def _source_is_already_imported(
    path: Path,
    size: int,
    modified_ns: int,
    library_root: Path,
    receipts: dict[str, dict[str, Any]],
) -> bool:
    receipt = receipts.get(str(path.resolve()))
    if not receipt:
        return False
    try:
        unchanged = int(receipt.get("size_bytes", -1)) == size and int(
            receipt.get("modified_ns", -1)
        ) == modified_ns
    except (TypeError, ValueError):
        return False
    if not unchanged:
        return False
    relative = str(receipt.get("paperpack_relative_path", ""))
    if not relative:
        return False
    try:
        paperpack = _resolved_library_path(library_root, relative)
    except LibraryWorkflowError:
        return False
    if not paperpack.is_file() or paperpack.suffix.casefold() != PAPERPACK_SUFFIX:
        return False
    try:
        inspect_paperpack(paperpack)
    except (OSError, PaperPackError):
        return False
    return True


def _record_imported_source(
    library_root: Path,
    source: Path,
    paperpack: Path,
    file_sha256: str,
) -> None:
    stat = source.stat()
    records = _load_import_receipts(library_root)
    records[str(source.resolve())] = {
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
        "file_sha256": file_sha256,
        "paperpack_relative_path": paperpack.relative_to(library_root).as_posix(),
        "imported_at": _now_iso(),
    }
    _atomic_json_write(
        _import_receipts_path(library_root),
        {
            "schema_version": 1,
            "updated_at": _now_iso(),
            "sources": records,
        },
    )


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolved_library_path(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise LibraryWorkflowError("라이브러리 상대 경로가 올바르지 않습니다.")
    resolved = (root / Path(relative_path)).resolve()
    if not _inside(root, resolved):
        raise LibraryWorkflowError("라이브러리 밖을 가리키는 경로는 사용할 수 없습니다.")
    return resolved


def _safe_component(value: str, fallback: str) -> str:
    cleaned = " ".join(_INVALID_FILENAME_RE.sub(" ", value).split()).rstrip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:80].rstrip(" .") or fallback


def _unique_destination(directory: Path, original_name: str) -> Path:
    stem = _safe_component(Path(original_name).stem, "paper")
    candidate = directory / f"{stem}.pdf"
    number = 2
    while candidate.exists() or Path(f"{candidate}{SIDECAR_SUFFIX}").exists():
        candidate = directory / f"{stem} ({number}).pdf"
        number += 1
    return candidate


def _unique_paperpack_destination(directory: Path, original_name: str) -> Path:
    stem = _safe_component(Path(original_name).stem, "paper")
    candidate = directory / f"{stem}{PAPERPACK_SUFFIX}"
    number = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({number}){PAPERPACK_SUFFIX}"
        number += 1
    return candidate


def _identity_from_record(record: dict[str, Any]) -> DocumentIdentity:
    identity = record.get("identity")
    file_data = record.get("file")
    if not isinstance(identity, dict) or not isinstance(file_data, dict):
        raise ValueError("identity and file objects are required")
    wrappers = tuple(
        WrapperPage(
            pdf_page=int(item.get("pdf_page", 0)),
            kind=str(item.get("kind") or item.get("type") or "unknown"),
            confidence=float(item.get("confidence", 0)),
        )
        for item in identity.get("wrapper_pages", [])
        if isinstance(item, dict) and int(item.get("pdf_page", 0)) > 0
    )
    file_hash = str(identity.get("file_sha256") or file_data.get("sha256") or "")
    return DocumentIdentity(
        file_id=str(identity.get("file_id") or f"sha256:{file_hash}"),
        edition_id=str(identity.get("edition_id") or f"sha256:{file_hash}"),
        work_id=str(identity["work_id"]),
        file_sha256=file_hash,
        content_fingerprint=str(identity.get("content_fingerprint", "")),
        segment_fingerprints=tuple(identity.get("segment_fingerprints", [])),
        fingerprint_version=str(identity.get("fingerprint_version", "paper-content-v1")),
        doi=identity.get("doi"),
        source_variant=str(identity.get("source_variant", "unknown")),
        wrapper_pages=wrappers,
        content_start_pdf_page=int(identity.get("content_start_pdf_page", 1)),
        page_count=int(identity.get("page_count") or file_data.get("page_count") or 0),
    )


def _metadata_from_record(record: dict[str, Any]) -> EditablePaperMetadata:
    bibliography = record.get("bibliography", {})
    classification = record.get("classification", {})
    description = record.get("description", {})
    raw_year = bibliography.get("year")
    try:
        year = int(raw_year) if raw_year not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    return EditablePaperMetadata(
        title=str(bibliography.get("title", "")),
        authors=[str(value) for value in bibliography.get("authors", [])],
        year=year,
        venue=str(bibliography.get("venue", "")),
        category=str(classification.get("category") or "Uncategorized"),
        subcategory=str(classification.get("subcategory") or "General"),
        tags=[str(value) for value in classification.get("tags", [])],
        summary_ko=str(description.get("summary_ko", "")),
    )


def _default_metadata(path: Path, page_texts: list[str]) -> EditablePaperMetadata:
    pdf_title = ""
    pdf_author = ""
    try:
        document = fitz.open(path)
        try:
            pdf_title = str(document.metadata.get("title") or "").strip()
            pdf_author = str(document.metadata.get("author") or "").strip()
        finally:
            document.close()
    except Exception:
        pass
    lines = [
        " ".join(line.split())
        for line in (page_texts[0].splitlines() if page_texts else [])
        if 5 <= len(" ".join(line.split())) <= 240
    ]
    title = pdf_title or (lines[0] if lines else path.stem)
    authors = [value.strip() for value in re.split(r"[;,]", pdf_author) if value.strip()]
    beginning = " ".join(page_texts[:3])
    match = _YEAR_RE.search(beginning)
    return EditablePaperMetadata(
        title=title,
        authors=authors,
        year=int(match.group(0)) if match else None,
    )


def _detection(page_texts: list[str]) -> tuple[str, str]:
    text = " ".join(page_texts).casefold()
    if len(text.strip()) < 500:
        return "needs_ocr", "추출된 본문이 너무 적어 OCR 또는 수동 확인이 필요합니다."
    markers = [marker for marker in ("abstract", "introduction", "references", "doi") if marker in text]
    if len(markers) >= 2:
        return "academic_likely", f"학술 문서 표식 확인: {', '.join(markers)}"
    return "needs_review", "학술 논문 구조가 충분히 확인되지 않아 사용자 검토가 필요합니다."


def _library_references(root: Path) -> Iterable[tuple[dict[str, Any], Path, Path, DocumentIdentity]]:
    seen_file_ids: set[str] = set()
    for record_path in iter_record_paths(root):
        try:
            record = load_record(record_path)
            storage_path = _resolved_library_path(
                root, str(record["file"]["relative_path"])
            )
            if record_path.suffix.casefold() == PAPERPACK_SUFFIX:
                storage_path = record_path.resolve()
            identity = _identity_from_record(record)
            if identity.file_id in seen_file_ids:
                continue
            seen_file_ids.add(identity.file_id)
            yield record, storage_path, record_path, identity
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            LibraryWorkflowError,
            PaperPackError,
        ):
            continue


def _best_duplicate(
    identity: DocumentIdentity,
    references: Iterable[tuple[dict[str, Any], Path, Path, DocumentIdentity]],
) -> DuplicateReference | None:
    ranking = {
        DuplicateKind.EXACT_FILE: 3,
        DuplicateKind.SAME_WORK: 2,
        DuplicateKind.POSSIBLE_RELATED: 1,
        DuplicateKind.DIFFERENT: 0,
    }
    best: DuplicateReference | None = None
    for record, pdf_path, sidecar, existing_identity in references:
        match = compare_identities(identity, existing_identity)
        if match.kind == DuplicateKind.DIFFERENT:
            continue
        bibliography = record.get("bibliography", {})
        candidate = DuplicateReference(
            match=match,
            title=str(bibliography.get("title") or pdf_path.stem),
            pdf_path=pdf_path,
            sidecar_path=sidecar,
            source_variant=existing_identity.source_variant,
        )
        if best is None or (ranking[match.kind], match.score) > (
            ranking[best.match.kind],
            best.match.score,
        ):
            best = candidate
    return best


class LibraryWorkflowController:
    """Stateful controller used by the low-resource periodic scanner and GUI."""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings_path = settings_path or default_settings_path()
        self._tracker = DiscoveryTracker()
        self._cache: dict[Path, tuple[int, int, ReviewItem]] = {}
        self._library_cache: list[LibraryEntry] | None = None

    def settings(self) -> AppSettings:
        return load_settings(self._settings_path)

    def configured_paths(self) -> tuple[Path, Path]:
        settings = self.settings()
        return (
            Path(settings.input_dir) if settings.input_dir else default_input_dir(),
            Path(settings.library_root) if settings.library_root else default_library_root(),
        )

    def save_paths(
        self,
        input_dir: Path,
        library_root: Path,
        *,
        auto_enabled: bool,
        resource_profile: str | None = None,
        scan_interval_seconds: int | None = None,
        remove_source_after_import: bool | None = None,
        auto_organize_academic: bool | None = None,
        focus_categories: list[str] | None = None,
    ) -> AppSettings:
        input_path = input_dir.expanduser().resolve()
        library_path = library_root.expanduser().resolve()
        if not input_path.is_dir():
            raise LibraryWorkflowError("입력 폴더가 존재하지 않습니다.")
        if input_path == library_path:
            raise LibraryWorkflowError("입력 폴더와 라이브러리 폴더는 달라야 합니다.")
        settings = self.settings()
        settings.input_dir = str(input_path)
        settings.library_root = str(library_path)
        settings.auto_enabled = bool(auto_enabled)
        if resource_profile is not None:
            settings.resource_profile = resource_profile
        if scan_interval_seconds is not None:
            settings.scan_interval_seconds = scan_interval_seconds
        if remove_source_after_import is not None:
            settings.remove_source_after_import = bool(remove_source_after_import)
        if auto_organize_academic is not None:
            settings.auto_organize_academic = bool(auto_organize_academic)
        if focus_categories is not None:
            settings.focus_categories = [
                name.strip() for name in focus_categories if name.strip()
            ]
        save_settings(settings, self._settings_path)
        self._library_cache = None
        return settings

    def scan(self) -> ReviewScan:
        settings = self.settings()
        input_dir, library_root = self.configured_paths()
        if not input_dir.is_dir():
            raise LibraryWorkflowError("입력 폴더가 존재하지 않습니다. 설정에서 지정하세요.")
        receipts = _load_import_receipts(library_root)
        candidates = list(iter_pdf_candidates(input_dir))
        active_candidates: list[Path] = []
        for path in candidates:
            try:
                stat = path.stat()
            except OSError:
                continue
            if not _source_is_already_imported(
                path, stat.st_size, stat.st_mtime_ns, library_root, receipts
            ):
                active_candidates.append(path)
        stable = [
            found
            for found in self._tracker.scan(
                input_dir,
                minimum_age_seconds=settings.minimum_age_seconds,
            )
            if found.path in active_candidates
        ]
        references = tuple(_library_references(library_root)) if library_root.is_dir() else ()
        items: list[ReviewItem] = []
        problems: list[ScanProblem] = []
        for found in stable:
            cached = self._cache.get(found.path)
            key = (found.observation.size, found.observation.modified_ns)
            if cached and cached[:2] == key:
                items.append(cached[2])
                continue
            try:
                page_texts = extract_page_texts(found.path)
                identity = build_identity_from_pages(sha256_file(found.path), page_texts)
                status, reason = _detection(page_texts)
                item = ReviewItem(
                    path=found.path,
                    identity=identity,
                    metadata=_default_metadata(found.path, page_texts),
                    detection_status=status,
                    detection_reason=reason,
                    duplicate=_best_duplicate(identity, references),
                    page_texts=tuple(page_texts),
                )
                self._cache[found.path] = (*key, item)
                try:
                    self._queue().enqueue(
                        path=item.path,
                        file_sha256=item.identity.file_sha256,
                        title=item.metadata.title,
                    )
                except (OSError, AnalysisQueueError) as exc:
                    problems.append(ScanProblem(found.path, str(exc)))
                items.append(item)
            except Exception as exc:
                problems.append(ScanProblem(found.path, str(exc)))
        candidate_set = {path.resolve() for path in active_candidates}
        self._cache = {
            path: value for path, value in self._cache.items() if path.resolve() in candidate_set
        }
        auto_organized: list[str] = []
        if settings.auto_organize_academic:
            items, auto_organized = self._auto_organize(items, problems)
        return ReviewScan(
            items=tuple(sorted(items, key=lambda item: item.path.name.casefold())),
            pending_stability=max(0, len(active_candidates) - len(stable)),
            problems=tuple(problems),
            auto_organized=tuple(auto_organized),
        )

    def _auto_organize(
        self, items: list[ReviewItem], problems: list[ScanProblem]
    ) -> tuple[list[ReviewItem], list[str]]:
        """Store confidently academic, non-duplicate PDFs without asking.

        중복 후보가 있거나 학술 논문으로 확신되지 않은 항목은 그대로 두어
        사람이 수집 화면에서 검토한다. 개별 실패도 검토 대상으로 남긴다.
        """

        remaining: list[ReviewItem] = []
        organized_titles: list[str] = []
        for item in items:
            if item.detection_status != "academic_likely" or item.duplicate is not None:
                remaining.append(item)
                continue
            try:
                metadata = self.suggest_metadata(item)
                result = self.organize(item, metadata, field_source="auto:regex")
            except Exception as exc:
                problems.append(ScanProblem(item.path, f"자동 보관 실패: {exc}"))
                remaining.append(item)
                continue
            organized_titles.append(metadata.title)
            del result
        return remaining, organized_titles

    def suggest_metadata(self, item: ReviewItem) -> EditablePaperMetadata:
        """Fill category, subcategory and venue with the regex first pass."""

        metadata = EditablePaperMetadata(
            title=item.metadata.title,
            authors=list(item.metadata.authors),
            year=item.metadata.year,
            venue=item.metadata.venue,
            category=item.metadata.category,
            subcategory=item.metadata.subcategory,
            tags=list(item.metadata.tags),
            summary_ko=item.metadata.summary_ko,
        )
        page_texts = list(item.page_texts)
        if not page_texts:
            return metadata
        settings = self.settings()
        try:
            result = classify_text(
                metadata.title,
                page_texts,
                allowed_categories=settings.focus_categories or None,
            )
        except TaxonomyError:
            result = None
        if result is not None and result.classified:
            metadata.category = result.category
            metadata.subcategory = result.subcategory
        if not metadata.venue:
            metadata.venue = extract_venue(page_texts)
        return metadata

    def organize(
        self,
        item: ReviewItem,
        metadata: EditablePaperMetadata,
        *,
        field_source: str = "user",
    ) -> OrganizedPaper:
        _validate_metadata(metadata)
        _input_dir, library_root = self.configured_paths()
        settings = self.settings()
        source = item.path.resolve()
        if not source.is_file():
            raise LibraryWorkflowError("원본 PDF를 찾을 수 없습니다.")
        if sha256_file(source) != item.identity.file_sha256:
            raise LibraryWorkflowError("검토 후 PDF 내용이 바뀌었습니다. 다시 검색하세요.")
        category = _safe_component(metadata.category, "Uncategorized")
        subcategory = _safe_component(metadata.subcategory, "General")
        destination_dir = library_root / "papers" / category / subcategory
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = _unique_paperpack_destination(destination_dir, source.name)
        record = _new_sidecar(
            item, metadata, source, destination, library_root, field_source
        )
        page_texts = list(item.page_texts)
        if not page_texts:
            try:
                page_texts = extract_page_texts(source)
            except PdfIdentityError:
                page_texts = []
        content = build_content_payload(page_texts)
        try:
            import_result = import_pdf_to_paperpack(
                destination,
                source,
                record,
                content=content,
                remove_source=settings.remove_source_after_import,
            )
            rebuild_library_index(library_root)
        except Exception as exc:
            try:
                if destination.exists() and not source.exists():
                    extract_paperpack_pdf(destination, source)
                if destination.exists():
                    destination.unlink()
            except (OSError, PaperPackError):
                pass
            try:
                rebuild_library_index(library_root)
            except Exception:
                pass
            raise LibraryWorkflowError(f"논문 이동을 완료하지 못했습니다: {exc}") from None
        self._tracker.forget(source)
        self._cache.pop(source, None)
        self._library_cache = None
        warnings: list[str] = []
        if not import_result.source_removed:
            try:
                _record_imported_source(
                    library_root,
                    source,
                    destination,
                    item.identity.file_sha256,
                )
            except OSError as exc:
                warnings.append(f"입력 PDF 처리 기록: {exc}")
        try:
            self._queue().relocate(
                item.identity.file_sha256,
                destination,
                status="organized_pending_analysis",
                title=metadata.title,
            )
        except (OSError, AnalysisQueueError) as exc:
            warnings.append(f"분석 큐: {exc}")
        index_warning = self._index_search_entry(destination)
        if index_warning:
            warnings.append(index_warning)
        return OrganizedPaper(destination, destination, "; ".join(warnings))

    def trash_confirmed_duplicate(self, item: ReviewItem) -> TrashOperation:
        if item.duplicate is None or not item.duplicate.confirmed:
            raise LibraryWorkflowError("확인된 중복 파일만 휴지통으로 이동할 수 있습니다.")
        _input_dir, library_root = self.configured_paths()
        source = item.path.resolve()
        if not source.is_file() or sha256_file(source) != item.identity.file_sha256:
            raise LibraryWorkflowError("파일이 없거나 검토 후 내용이 바뀌었습니다.")
        operation_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        operation_dir = library_root / "trash" / operation_id
        operation_dir.mkdir(parents=True, exist_ok=False)
        destination = operation_dir / source.name
        manifest = operation_dir / "manifest.json"
        moved = False
        try:
            shutil.move(str(source), str(destination))
            moved = True
            _atomic_json_write(
                manifest,
                {
                    "schema_version": 1,
                    "operation_id": operation_id,
                    "kind": "unorganized_duplicate",
                    "created_at": _now_iso(),
                    "original_path": str(source),
                    "trashed_name": destination.name,
                    "sha256": item.identity.file_sha256,
                    "duplicate_of": str(item.duplicate.pdf_path),
                    "restored_at": None,
                },
            )
        except Exception as exc:
            if moved and destination.exists() and not source.exists():
                shutil.move(str(destination), str(source))
            raise LibraryWorkflowError(f"휴지통 이동을 완료하지 못했습니다: {exc}") from None
        self._tracker.forget(source)
        self._cache.pop(source, None)
        try:
            self._queue().remove(f"sha256:{item.identity.file_sha256}")
        except AnalysisQueueError:
            pass
        return TrashOperation(operation_id, manifest, destination)

    def list_trash(self) -> list[TrashEntry]:
        _input_dir, root = self.configured_paths()
        trash_root = root / "trash"
        entries: list[TrashEntry] = []
        if not trash_root.is_dir():
            return entries
        for manifest in trash_root.glob("*/manifest.json"):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if data.get("restored_at"):
                    continue
                operation_id = str(data["operation_id"])
                trashed = manifest.parent / str(data["trashed_name"])
                if not trashed.is_file():
                    continue
                entries.append(
                    TrashEntry(
                        operation_id=operation_id,
                        manifest_path=manifest,
                        original_path=Path(str(data["original_path"])),
                        trashed_path=trashed,
                        duplicate_of=Path(str(data.get("duplicate_of", ""))),
                    )
                )
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return sorted(entries, key=lambda entry: entry.operation_id, reverse=True)

    def restore_trash(self, entry: TrashEntry) -> Path:
        input_dir, root = self.configured_paths()
        manifest = entry.manifest_path.resolve()
        if not _inside((root / "trash").resolve(), manifest):
            raise LibraryWorkflowError("앱 휴지통 밖의 작업은 복원할 수 없습니다.")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("restored_at"):
            raise LibraryWorkflowError("이미 복원된 작업입니다.")
        trashed = manifest.parent / str(data["trashed_name"])
        if not trashed.is_file() or sha256_file(trashed) != str(data["sha256"]):
            raise LibraryWorkflowError("휴지통 파일이 없거나 내용이 바뀌었습니다.")
        requested = Path(str(data["original_path"]))
        requested_is_in_input = _inside(input_dir, requested.parent)
        destination_dir = requested.parent if requested_is_in_input else input_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        preferred = requested if requested_is_in_input else destination_dir / requested.name
        destination = (
            preferred
            if not preferred.exists()
            else _unique_destination(destination_dir, requested.name)
        )
        shutil.move(str(trashed), str(destination))
        data["restored_at"] = _now_iso()
        data["restored_path"] = str(destination)
        try:
            _atomic_json_write(manifest, data)
        except Exception as exc:
            if destination.exists() and not trashed.exists():
                shutil.move(str(destination), str(trashed))
            raise LibraryWorkflowError(f"복원 기록을 저장하지 못했습니다: {exc}") from None
        self._tracker.forget(destination)
        try:
            self._queue().enqueue(
                path=destination,
                file_sha256=str(data["sha256"]),
                title=destination.stem,
            )
        except (OSError, AnalysisQueueError):
            pass
        return destination

    def analysis_queue(self) -> list[AnalysisQueueItem]:
        return self._queue().load()

    def recover_interrupted_analysis(self) -> int:
        return self._queue().recover_interrupted()

    def claim_next_analysis(self) -> AnalysisQueueItem | None:
        return self._queue().claim_next()

    def complete_analysis(self, queue_id: str) -> AnalysisQueueItem:
        return self._queue().mark_completed(queue_id)

    def fail_analysis(self, queue_id: str, message: str) -> AnalysisQueueItem:
        return self._queue().mark_failed(queue_id, message)

    def retry_queue_item(self, queue_id: str, *, high: bool = False) -> AnalysisQueueItem:
        return self._queue().retry(queue_id, high=high)

    def set_background_analysis_enabled(self, enabled: bool) -> AppSettings:
        settings = self.settings()
        settings.background_analysis_enabled = bool(enabled)
        save_settings(settings, self._settings_path)
        return settings

    def apply_analysis_result(
        self,
        source_path: Path,
        execution: SummaryExecution,
    ) -> None:
        """Persist a verified summary without overwriting non-empty curated fields."""

        _input_dir, root = self.configured_paths()
        source = source_path.expanduser().resolve()
        papers_root = (root / "papers").resolve()
        if (
            source.suffix.casefold() != PAPERPACK_SUFFIX
            or not source.is_file()
            or not _inside(papers_root, source)
        ):
            raise LibraryWorkflowError(
                "백그라운드 분석 결과는 라이브러리의 paperpack에만 저장할 수 있습니다."
            )
        record = load_paperpack_metadata(source)
        now = _now_iso()
        result = execution.result
        data = result.data
        record["analysis"] = {
            "status": "completed",
            "analysis_level": execution.preview.mode.value,
            "summary_ko": data.summary_ko,
            "research_question": data.research_question,
            "methods": list(data.methods),
            "contributions": list(data.contributions),
            "limitations": list(data.limitations),
            "keywords": list(data.keywords),
            "completed_at": now,
            "provenance": execution.provenance,
        }
        description = record.setdefault("description", {})
        curation = record.setdefault("curation", {})
        locked = set(curation.get("locked_fields", []))
        sources = curation.setdefault("field_sources", {})
        values = {
            "summary_ko": data.summary_ko,
            "research_question": data.research_question,
            "methods": list(data.methods),
            "contributions": list(data.contributions),
            "limitations": list(data.limitations),
            "keywords": list(data.keywords),
        }
        for name, value in values.items():
            field = f"description.{name}"
            if field in locked or description.get(name):
                continue
            description[name] = value
            sources[field] = f"ai:{result.provider}"
        curation["revision"] = int(curation.get("revision", 0)) + 1
        curation["last_edited_at"] = now
        curation["last_edited_by"] = f"ai:{result.provider}"
        workflow = record.setdefault("workflow", {})
        workflow.update(
            {
                "analysis_status": "completed",
                "needs_reanalysis": False,
                "updated_at": now,
            }
        )
        provenance = record.setdefault("provenance", {})
        provenance["summary"] = execution.provenance
        try:
            update_paperpack(source, record, changed_by=f"ai:{result.provider}")
            rebuild_library_index(root)
        except (OSError, PaperPackError) as exc:
            raise LibraryWorkflowError(f"AI 분석 결과를 저장하지 못했습니다: {exc}") from None
        self._index_search_entry(source)
        self._library_cache = None

    def backfill_content(self, *, progress=None) -> tuple[int, tuple[str, ...]]:
        """Fill empty content/content.json entries by re-extracting PDF text.

        검색 색인은 이 본문에서 재생성되므로, content가 비어 있던 기존
        paperpack도 재추출해 검색 대상으로 만든다.
        """

        _input_dir, root = self.configured_paths()
        problems: list[str] = []
        filled = 0
        packs = sorted(iter_paperpacks(root))
        for index, paperpack in enumerate(packs, start=1):
            if progress is not None:
                progress(index, len(packs), paperpack.name)
            try:
                if content_pages(load_paperpack_content(paperpack)):
                    continue
                pdf_path = self.materialize_pdf(paperpack)
                payload = build_content_payload(extract_page_texts(pdf_path))
                if not payload["pages"]:
                    continue
                update_paperpack(
                    paperpack,
                    load_paperpack_metadata(paperpack),
                    content=payload,
                    changed_by="content-backfill",
                )
                self._index_search_entry(paperpack)
                filled += 1
            except (
                OSError,
                ValueError,
                PdfIdentityError,
                PaperPackError,
                LibraryWorkflowError,
            ) as exc:
                problems.append(f"{paperpack.name}: {exc}")
        if filled:
            self._library_cache = None
        return filled, tuple(problems)

    def materialize_pdf(self, path: Path) -> Path:
        """Return a real PDF path, extracting a verified paperpack lazily."""

        source = path.expanduser().resolve()
        if source.suffix.casefold() == ".pdf":
            if not source.is_file():
                raise LibraryWorkflowError("PDF 파일을 찾을 수 없습니다.")
            return source
        if source.suffix.casefold() != PAPERPACK_SUFFIX or not source.is_file():
            raise LibraryWorkflowError("PDF 또는 paperpack 파일을 찾을 수 없습니다.")
        _input_dir, root = self.configured_paths()
        if not _inside((root / "papers").resolve(), source):
            raise LibraryWorkflowError("라이브러리 밖의 paperpack은 열 수 없습니다.")
        try:
            info = inspect_paperpack(source)
            cached = root / "cache" / "pdf" / f"{info.pdf_sha256}.pdf"
            if cached.is_file() and sha256_file(cached) == info.pdf_sha256:
                return cached
            return extract_paperpack_pdf(source, cached)
        except (OSError, PaperPackError) as exc:
            raise LibraryWorkflowError(f"paperpack PDF를 열 수 없습니다: {exc}") from None

    def materialize_editable_pdf(self, path: Path) -> Path:
        """Return an isolated PDF working copy for sPDF editing."""

        source = path.expanduser().resolve()
        if source.suffix.casefold() == ".pdf":
            return self.materialize_pdf(source)
        source, workspace_pdf, state_path = self._paperpack_edit_paths(source)
        if workspace_pdf.exists() != state_path.exists():
            raise LibraryWorkflowError(
                "paperpack 편집 작업공간이 불완전합니다. 편집본 폐기 후 다시 여세요."
            )
        if workspace_pdf.is_file():
            status = self.paperpack_working_copy(source)
            if status is None:
                raise LibraryWorkflowError("paperpack 편집 상태를 읽을 수 없습니다.")
            if status.changed or not status.conflicted:
                return workspace_pdf
        try:
            info = inspect_paperpack(source)
            extract_paperpack_pdf(source, workspace_pdf)
            _atomic_json_write(
                state_path,
                {
                    "schema_version": 1,
                    "paperpack_path": str(source),
                    "base_pdf_sha256": info.pdf_sha256,
                    "base_revision": info.revision,
                    "created_at": _now_iso(),
                },
            )
            return workspace_pdf
        except (OSError, PaperPackError) as exc:
            raise LibraryWorkflowError(
                f"paperpack 편집본을 준비할 수 없습니다: {exc}"
            ) from None

    def paperpack_working_copy(
        self, path: Path
    ) -> PaperPackWorkingCopy | None:
        """Inspect a saved working copy without changing either copy."""

        source, workspace_pdf, state_path = self._paperpack_edit_paths(path)
        if not workspace_pdf.exists() and not state_path.exists():
            return None
        if not workspace_pdf.is_file() or not state_path.is_file():
            raise LibraryWorkflowError("paperpack 편집 작업공간이 불완전합니다.")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("schema_version") != 1:
                raise ValueError("unsupported edit state")
            if Path(str(state["paperpack_path"])).resolve() != source:
                raise ValueError("working copy belongs to another paperpack")
            base_sha256 = str(state["base_pdf_sha256"])
            base_revision = int(state["base_revision"])
            current_sha256 = sha256_file(workspace_pdf)
            info = inspect_paperpack(source)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
            PaperPackError,
        ) as exc:
            raise LibraryWorkflowError(
                f"paperpack 편집 상태를 읽을 수 없습니다: {exc}"
            ) from None
        return PaperPackWorkingCopy(
            paperpack_path=source,
            pdf_path=workspace_pdf,
            base_pdf_sha256=base_sha256,
            current_pdf_sha256=current_sha256,
            base_revision=base_revision,
            current_revision=info.revision,
            changed=current_sha256 != base_sha256,
            conflicted=(
                info.pdf_sha256 != base_sha256 or info.revision != base_revision
            ),
        )

    def apply_paperpack_working_copy(self, path: Path) -> PaperPackPdfUpdate:
        """Commit a saved sPDF working copy as a verified paperpack revision."""

        status = self.paperpack_working_copy(path)
        if status is None:
            raise LibraryWorkflowError("적용할 paperpack 편집본이 없습니다.")
        if not status.changed:
            raise LibraryWorkflowError("편집본에 저장된 변경이 없습니다.")
        if status.conflicted:
            raise LibraryWorkflowError(
                "편집 중 paperpack이 변경되었습니다. 편집본을 덮어쓰지 않았습니다."
            )
        try:
            page_texts = extract_page_texts(status.pdf_path)
            new_identity = build_identity_from_pages(
                status.current_pdf_sha256, page_texts
            )
            record = load_paperpack_metadata(status.paperpack_path)
            now = _now_iso()
            record["id"] = new_identity.file_id
            record["identity"] = new_identity.to_dict()
            file_data = record.setdefault("file", {})
            file_data.update(
                {
                    "sha256": new_identity.file_sha256,
                    "size_bytes": status.pdf_path.stat().st_size,
                    "page_count": new_identity.page_count,
                }
            )
            workflow = record.setdefault("workflow", {})
            workflow.update(
                {
                    "status": "organized",
                    "needs_reanalysis": True,
                    "content_stale": False,
                    "pdf_edited_at": now,
                    "updated_at": now,
                }
            )
            curation = record.setdefault("curation", {})
            curation.update(
                {
                    "revision": int(curation.get("revision", 0)) + 1,
                    "last_edited_at": now,
                    "last_edited_by": "user:spdf",
                }
            )
            record.setdefault("provenance", {})["last_pdf_editor"] = "sPDF"
            info = replace_paperpack_pdf(
                status.paperpack_path,
                status.pdf_path,
                record,
                content=build_content_payload(page_texts),
                expected_pdf_sha256=status.base_pdf_sha256,
                expected_revision=status.base_revision,
                changed_by="user:spdf",
            )
        except (OSError, ValueError, PdfIdentityError, PaperPackError) as exc:
            raise LibraryWorkflowError(
                f"편집본을 paperpack에 적용할 수 없습니다: {exc}"
            ) from None

        _source, _workspace_pdf, state_path = self._paperpack_edit_paths(
            status.paperpack_path
        )
        _atomic_json_write(
            state_path,
            {
                "schema_version": 1,
                "paperpack_path": str(status.paperpack_path),
                "base_pdf_sha256": info.pdf_sha256,
                "base_revision": info.revision,
                "created_at": _now_iso(),
            },
        )
        old_cache = (
            self.configured_paths()[1]
            / "cache"
            / "pdf"
            / f"{status.base_pdf_sha256}.pdf"
        )
        try:
            old_cache.unlink(missing_ok=True)
            status.pdf_path.with_suffix(status.pdf_path.suffix + ".bak").unlink(
                missing_ok=True
            )
        except OSError:
            pass

        warnings: list[str] = []
        try:
            title = str(
                record.get("bibliography", {}).get("title")
                or status.paperpack_path.stem
            )
            self._queue().replace_file(
                status.base_pdf_sha256,
                info.pdf_sha256,
                status.paperpack_path,
                title=title,
            )
            rebuild_library_index(self.configured_paths()[1])
        except (OSError, AnalysisQueueError) as exc:
            warnings.append(f"파생 색인 갱신: {exc}")
        index_warning = self._index_search_entry(status.paperpack_path)
        if index_warning:
            warnings.append(index_warning)
        self._library_cache = None
        return PaperPackPdfUpdate(
            paperpack_path=status.paperpack_path,
            working_pdf_path=status.pdf_path,
            previous_pdf_sha256=status.base_pdf_sha256,
            pdf_sha256=info.pdf_sha256,
            revision=info.revision,
            warning="; ".join(warnings),
        )

    def discard_paperpack_working_copy(self, path: Path) -> bool:
        """Remove only the isolated editable copy; never touch the paperpack."""

        _source, workspace_pdf, state_path = self._paperpack_edit_paths(path)
        existed = workspace_pdf.exists() or state_path.exists()
        for candidate in (
            workspace_pdf,
            workspace_pdf.with_suffix(workspace_pdf.suffix + ".bak"),
            state_path,
        ):
            try:
                candidate.unlink(missing_ok=True)
            except OSError as exc:
                raise LibraryWorkflowError(
                    f"paperpack 편집본을 폐기할 수 없습니다: {exc}"
                ) from None
        return existed

    def _paperpack_edit_paths(self, path: Path) -> tuple[Path, Path, Path]:
        source = path.expanduser().resolve()
        _input_dir, root = self.configured_paths()
        papers_root = (root / "papers").resolve()
        if (
            source.suffix.casefold() != PAPERPACK_SUFFIX
            or not source.is_file()
            or not _inside(papers_root, source)
        ):
            raise LibraryWorkflowError(
                "라이브러리 안의 paperpack만 편집 작업공간을 만들 수 있습니다."
            )
        key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
        edit_root = root / "cache" / "editing" / key
        return source, edit_root / "working.pdf", edit_root / "state.json"

    def set_queue_priority(self, queue_id: str, high: bool) -> AnalysisQueueItem:
        return self._queue().set_priority(queue_id, high)

    def remove_from_queue(self, queue_id: str) -> None:
        self._queue().remove(queue_id)

    def _queue(self) -> AnalysisQueueStore:
        _input_dir, root = self.configured_paths()
        return AnalysisQueueStore(root)

    def list_library(self, query: str = "") -> list[LibraryEntry]:
        _input_dir, root = self.configured_paths()
        normalized_query = " ".join(query.casefold().split())
        if self._library_cache is None:
            entries: list[LibraryEntry] = []
            if root.is_dir():
                for record, pdf_path, sidecar, identity in _library_references(root):
                    entries.append(
                        LibraryEntry(
                            pdf_path=pdf_path,
                            sidecar_path=sidecar,
                            metadata=_metadata_from_record(record),
                            work_id=identity.work_id,
                            source_variant=identity.source_variant,
                            record=record,
                        )
                    )
            self._library_cache = sorted(
                entries, key=lambda entry: entry.metadata.title.casefold()
            )
        if not normalized_query:
            return list(self._library_cache)
        matches: list[LibraryEntry] = []
        for entry in self._library_cache:
            metadata = entry.metadata
            haystack = " ".join(
                [
                    metadata.title,
                    *metadata.authors,
                    str(metadata.year or ""),
                    metadata.venue,
                    metadata.category,
                    metadata.subcategory,
                    *metadata.tags,
                    metadata.summary_ko,
                    json.dumps(
                        entry.record.get("experimental_details", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ]
            ).casefold()
            if normalized_query and normalized_query not in " ".join(haystack.split()):
                continue
            matches.append(entry)
        return matches

    def invalidate_library_cache(self) -> None:
        self._library_cache = None

    def rebuild_search_index(self, *, progress=None) -> tuple[int, tuple[str, ...]]:
        """Rebuild the disposable full-text cache from every paperpack."""

        _input_dir, root = self.configured_paths()
        try:
            return rebuild_search_index(root, progress=progress)
        except SearchIndexError as exc:
            raise LibraryWorkflowError(f"검색 색인을 만들 수 없습니다: {exc}") from None

    def search_library(self, query: str, *, limit: int = 50) -> list[LibraryEntry]:
        """Return library entries whose stored full text matches the query."""

        normalized = " ".join(query.split())
        if not normalized:
            return self.list_library()
        _input_dir, root = self.configured_paths()
        try:
            hits: list[SearchHit] = search_full_text(root, normalized, limit=limit)
        except SearchIndexError:
            return self.list_library(normalized)
        if not hits:
            return self.list_library(normalized)
        by_path = {
            entry.pdf_path.resolve(): entry for entry in self.list_library()
        }
        entries: list[LibraryEntry] = []
        for hit in hits:
            try:
                path = _resolved_library_path(root, hit.relative_path)
            except LibraryWorkflowError:
                continue
            entry = by_path.get(path)
            if entry is not None:
                entries.append(entry)
        return entries or self.list_library(normalized)

    def _index_search_entry(self, paperpack: Path) -> str:
        """Update one search entry; failures are reported, never fatal."""

        _input_dir, root = self.configured_paths()
        try:
            update_search_entry(root, paperpack)
        except (OSError, SearchIndexError) as exc:
            return f"검색 색인: {exc}"
        return ""

    def legacy_migration_preview(self) -> LegacyMigrationPreview:
        _input_dir, root = self.configured_paths()
        return LegacyMigrationService(root).preview()

    def migrate_legacy_papers(
        self,
        metadata_paths: Iterable[Path],
        *,
        move_legacy_to_trash: bool = False,
    ) -> LegacyMigrationResult:
        _input_dir, root = self.configured_paths()
        result = LegacyMigrationService(root).migrate(
            metadata_paths,
            move_legacy_to_trash=move_legacy_to_trash,
        )
        self._library_cache = None
        return result

    def legacy_migration_trash(self) -> tuple[LegacyMigrationTrashEntry, ...]:
        _input_dir, root = self.configured_paths()
        return LegacyMigrationService(root).list_trash()

    def restore_legacy_migration(self, operation_id: str) -> tuple[Path, ...]:
        _input_dir, root = self.configured_paths()
        restored = LegacyMigrationService(root).restore_trash(operation_id)
        self._library_cache = None
        return restored

    def warm_startup_cache(self) -> StartupSnapshot:
        """Read lightweight JSON state for the splash startup worker."""
        _input_dir, root = self.configured_paths()
        problems: list[str] = []
        local_json = 0
        if root.is_dir():
            for path in root.rglob("*.json"):
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                    local_json += 1
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    problems.append(f"{path.name}: {exc}")
        return StartupSnapshot(
            library_entries=len(self.list_library()),
            local_json_files=local_json,
            problems=tuple(problems),
        )

    def update_library_metadata(
        self, entry: LibraryEntry, metadata: EditablePaperMetadata
    ) -> LibraryEntry:
        _validate_metadata(metadata)
        _input_dir, root = self.configured_paths()
        sidecar = entry.sidecar_path.resolve()
        papers_root = (root / "papers").resolve()
        is_paperpack = sidecar.suffix.casefold() == PAPERPACK_SUFFIX
        is_legacy_sidecar = sidecar.name.endswith(SIDECAR_SUFFIX)
        if not _inside(papers_root, sidecar) or not (
            is_paperpack or is_legacy_sidecar
        ):
            raise LibraryWorkflowError("라이브러리 밖의 색인은 수정할 수 없습니다.")
        current = load_record(sidecar)
        original = json.loads(json.dumps(current))
        curation = current.setdefault("curation", {})
        revision = int(curation.get("revision", 0)) + 1
        file_hash = str(current.get("file", {}).get("sha256") or "unknown").replace(":", "-")
        history = root / "history" / _safe_component(file_hash, "unknown")
        backup = history / f"revision-{revision - 1:04d}.paper.json"
        if is_legacy_sidecar and not backup.exists():
            _atomic_json_write(backup, original)
        _apply_metadata(current, metadata)
        curation.update(
            {
                "revision": revision,
                "field_sources": {
                    **curation.get("field_sources", {}),
                    "bibliography.title": "user",
                    "bibliography.authors": "user",
                    "bibliography.year": "user",
                    "bibliography.venue": "user",
                    "classification.category": "user",
                    "classification.subcategory": "user",
                    "classification.tags": "user",
                    "description.summary_ko": "user",
                },
                "last_edited_at": _now_iso(),
                "last_edited_by": "user",
            }
        )
        current.setdefault("workflow", {})["updated_at"] = _now_iso()
        try:
            if is_paperpack:
                update_paperpack(sidecar, current, changed_by="user")
            else:
                _atomic_json_write(sidecar, current)
            rebuild_library_index(root)
        except Exception as exc:
            if is_paperpack:
                try:
                    update_paperpack(sidecar, original, changed_by="rollback")
                except Exception:
                    pass
            else:
                _atomic_json_write(sidecar, original)
            try:
                rebuild_library_index(root)
            except Exception:
                pass
            raise LibraryWorkflowError(f"색인 수정을 저장하지 못했습니다: {exc}") from None
        if is_paperpack:
            self._index_search_entry(sidecar)
        self._library_cache = None
        return LibraryEntry(
            pdf_path=entry.pdf_path,
            sidecar_path=sidecar,
            metadata=metadata,
            work_id=entry.work_id,
            source_variant=entry.source_variant,
            record=current,
        )


def _validate_metadata(metadata: EditablePaperMetadata) -> None:
    if not metadata.title.strip():
        raise LibraryWorkflowError("제목을 입력하세요.")
    if metadata.year is not None and not 1000 <= metadata.year <= 9999:
        raise LibraryWorkflowError("연도는 네 자리 숫자로 입력하세요.")


def _apply_metadata(record: dict[str, Any], metadata: EditablePaperMetadata) -> None:
    record.setdefault("bibliography", {}).update(
        {
            "title": metadata.title.strip(),
            "authors": metadata.authors,
            "year": metadata.year,
            "venue": metadata.venue.strip(),
        }
    )
    record.setdefault("classification", {}).update(
        {
            "category": metadata.category.strip() or "Uncategorized",
            "subcategory": metadata.subcategory.strip() or "General",
            "tags": metadata.tags,
        }
    )
    record.setdefault("description", {})["summary_ko"] = metadata.summary_ko.strip()


def _new_sidecar(
    item: ReviewItem,
    metadata: EditablePaperMetadata,
    source: Path,
    destination: Path,
    library_root: Path,
    field_source: str = "user",
) -> dict[str, Any]:
    now = _now_iso()
    identity = item.identity.to_dict()
    identity["doi"] = item.identity.doi
    record: dict[str, Any] = {
        "schema_version": 2,
        "id": item.identity.file_id,
        "file": {
            "original_name": source.name,
            "current_name": destination.name,
            "relative_path": destination.relative_to(library_root).as_posix(),
            "sha256": item.identity.file_sha256,
            "size_bytes": source.stat().st_size,
            "page_count": item.identity.page_count,
        },
        "identity": identity,
        "bibliography": {"venue": "", "doi": item.identity.doi or "", "arxiv_id": ""},
        "classification": {"confidence": 0.0},
        "description": {
            "research_question": "",
            "methods": [],
            "contributions": [],
            "limitations": [],
            "keywords": [],
        },
        "experimental_details": {
            "culture_media": [],
            "cell_lines": [],
            "organisms": [],
            "reagents": [],
            "instruments": [],
            "datasets": [],
            "experimental_conditions": [],
        },
        "detection": {
            "is_academic_paper": item.detection_status == "academic_likely",
            "confidence": 0.85 if item.detection_status == "academic_likely" else 0.0,
            "reason": item.detection_reason,
        },
        "workflow": {
            "status": "organized",
            "needs_review": item.detection_status != "academic_likely",
            "review_reason": "" if item.detection_status == "academic_likely" else item.detection_reason,
            "processed_at": now,
            "updated_at": now,
        },
        "curation": {
            "revision": 1,
            "field_sources": {},
            "locked_fields": [],
            "last_edited_at": now,
            "last_edited_by": field_source,
        },
        "provenance": {"extractor": "pymupdf", "ocr_used": False},
    }
    _apply_metadata(record, metadata)
    record["curation"]["field_sources"] = {
        name: field_source
        for name in (
            "bibliography.title",
            "bibliography.authors",
            "bibliography.year",
            "bibliography.venue",
            "classification.category",
            "classification.subcategory",
            "classification.tags",
            "description.summary_ko",
        )
    }
    return record
