"""Manual-first collection, duplicate review and library editing workflow."""

from __future__ import annotations

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
from paper_organizer.application.cloud_metadata_sync import (
    CloudMetadataSyncError,
    CloudMetadataSynchronizer,
    MetadataConflict,
)
from paper_organizer.application.legacy_migration import (
    LegacyMigrationPreview,
    LegacyMigrationResult,
    LegacyMigrationService,
    LegacyMigrationTrashEntry,
)
from paper_organizer.core.discovery import DiscoveryTracker, iter_pdf_candidates
from paper_organizer.core.document_identity import (
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
    extract_paperpack_pdf,
    import_pdf_to_paperpack,
    inspect_paperpack,
    update_paperpack,
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


@dataclass(frozen=True, slots=True)
class ScanProblem:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ReviewScan:
    items: tuple[ReviewItem, ...]
    pending_stability: int
    problems: tuple[ScanProblem, ...]


@dataclass(frozen=True, slots=True)
class OrganizedPaper:
    pdf_path: Path
    sidecar_path: Path
    sync_warning: str = ""


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
    sync_warning: str = ""


@dataclass(frozen=True, slots=True)
class MetadataSyncResult:
    destination: Path | None
    copied_files: int
    problems: tuple[str, ...] = ()
    conflict_count: int = 0
    portable_path: Path | None = None


@dataclass(frozen=True, slots=True)
class StartupSnapshot:
    library_entries: int
    local_json_files: int
    synced_json_files: int
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


def _atomic_file_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp", dir=str(destination.parent)
    )
    try:
        with source.open("rb") as input_stream, os.fdopen(handle, "wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(temp_name, destination)
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
        metadata_sync_dir: Path | None = None,
        resource_profile: str | None = None,
        scan_interval_seconds: int | None = None,
        remove_source_after_import: bool | None = None,
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
        settings.metadata_sync_dir = (
            str(metadata_sync_dir.expanduser().resolve()) if metadata_sync_dir else ""
        )
        settings.auto_enabled = bool(auto_enabled)
        if resource_profile is not None:
            settings.resource_profile = resource_profile
        if scan_interval_seconds is not None:
            settings.scan_interval_seconds = scan_interval_seconds
        if remove_source_after_import is not None:
            settings.remove_source_after_import = bool(remove_source_after_import)
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
        queue_updated = False
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
                )
                self._cache[found.path] = (*key, item)
                try:
                    self._queue().enqueue(
                        path=item.path,
                        file_sha256=item.identity.file_sha256,
                        title=item.metadata.title,
                    )
                    queue_updated = True
                except (OSError, AnalysisQueueError) as exc:
                    problems.append(ScanProblem(found.path, str(exc)))
                items.append(item)
            except Exception as exc:
                problems.append(ScanProblem(found.path, str(exc)))
        if queue_updated:
            sync = self.sync_metadata()
            problems.extend(
                ScanProblem(library_root, f"JSON 미러: {message}")
                for message in sync.problems
            )
        candidate_set = {path.resolve() for path in active_candidates}
        self._cache = {
            path: value for path, value in self._cache.items() if path.resolve() in candidate_set
        }
        return ReviewScan(
            items=tuple(sorted(items, key=lambda item: item.path.name.casefold())),
            pending_stability=max(0, len(active_candidates) - len(stable)),
            problems=tuple(problems),
        )

    def organize(self, item: ReviewItem, metadata: EditablePaperMetadata) -> OrganizedPaper:
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
        record = _new_sidecar(item, metadata, source, destination, library_root)
        try:
            import_result = import_pdf_to_paperpack(
                destination,
                source,
                record,
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
        sync = self.sync_metadata()
        warnings.extend(sync.problems)
        warning = "; ".join(warnings)
        return OrganizedPaper(destination, destination, warning)

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
        self.sync_metadata()
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
        self.sync_metadata()
        return destination

    def analysis_queue(self) -> list[AnalysisQueueItem]:
        return self._queue().load()

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

    def set_queue_priority(self, queue_id: str, high: bool) -> AnalysisQueueItem:
        item = self._queue().set_priority(queue_id, high)
        self.sync_metadata()
        return item

    def remove_from_queue(self, queue_id: str) -> None:
        self._queue().remove(queue_id)
        self.sync_metadata()

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
        self.sync_metadata()
        return result

    def legacy_migration_trash(self) -> tuple[LegacyMigrationTrashEntry, ...]:
        _input_dir, root = self.configured_paths()
        return LegacyMigrationService(root).list_trash()

    def restore_legacy_migration(self, operation_id: str) -> tuple[Path, ...]:
        _input_dir, root = self.configured_paths()
        restored = LegacyMigrationService(root).restore_trash(operation_id)
        self._library_cache = None
        self.sync_metadata()
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
        settings = self.settings()
        synced_json = 0
        if settings.metadata_sync_dir:
            sync_root = Path(settings.metadata_sync_dir)
            if sync_root.is_dir():
                synced_json = sum(1 for _path in sync_root.rglob("*.json"))
        return StartupSnapshot(
            library_entries=len(self.list_library()),
            local_json_files=local_json,
            synced_json_files=synced_json,
            problems=tuple(problems),
        )

    def sync_metadata(self) -> MetadataSyncResult:
        """Export paperpack JSON and synchronize a separate portable edit file.

        Local paperpacks remain authoritative. Cloud edits are applied only when one
        side changed; concurrent changes are reported for explicit resolution.
        """
        settings = self.settings()
        if not settings.metadata_sync_dir:
            return MetadataSyncResult(None, 0)
        root = Path(settings.library_root).expanduser().resolve()
        destination = Path(settings.metadata_sync_dir).expanduser().resolve()
        if root == destination or _inside(root, destination):
            return MetadataSyncResult(
                destination, 0, ("JSON 동기화 폴더는 라이브러리 밖에 지정하세요.",)
            )
        sources: list[tuple[Path, Path]] = []
        paperpack_exports: list[tuple[Path, Path]] = []
        for record_path in iter_record_paths(root):
            relative = record_path.relative_to(root / "papers")
            if record_path.suffix.casefold() == PAPERPACK_SUFFIX:
                paperpack_exports.append(
                    (
                        record_path,
                        Path("backup")
                        / "paperpacks"
                        / relative.parent
                        / f"{record_path.name}.metadata.json",
                    )
                )
            else:
                sources.append(
                    (record_path, Path("backup") / "sidecars" / relative)
                )
        for section in ("index", "history", "state"):
            section_root = root / section
            if section_root.is_dir():
                for source in section_root.rglob("*.json"):
                    sources.append(
                        (
                            source,
                            Path("backup") / section / source.relative_to(section_root),
                        )
                    )
        copied = 0
        problems: list[str] = []
        for paperpack, relative in paperpack_exports:
            try:
                _atomic_json_write(destination / relative, load_record(paperpack))
                copied += 1
            except (OSError, ValueError, PaperPackError) as exc:
                problems.append(f"{relative.as_posix()}: {exc}")
        for source, relative in sources:
            try:
                _atomic_file_copy(source, destination / relative)
                copied += 1
            except OSError as exc:
                problems.append(f"{relative.as_posix()}: {exc}")
        try:
            outcome = CloudMetadataSynchronizer(root, destination).synchronize()
            if outcome.imported_records:
                self._library_cache = None
        except (OSError, CloudMetadataSyncError) as exc:
            outcome = None
            problems.append(f"portable-library.json: {exc}")
        try:
            _atomic_json_write(
                destination / "sync-manifest.json",
                {
                    "schema_version": 1,
                    "mode": "original-backup-plus-portable-sync",
                    "source_library": str(root),
                    "updated_at": _now_iso(),
                    "copied_files": copied,
                    "portable_file": "portable-library.json",
                    "conflict_count": len(outcome.conflicts) if outcome else 0,
                    "problems": problems,
                },
            )
        except OSError as exc:
            problems.append(f"sync-manifest.json: {exc}")
        return MetadataSyncResult(
            destination,
            copied,
            tuple(problems),
            conflict_count=len(outcome.conflicts) if outcome else 0,
            portable_path=outcome.portable_path if outcome else None,
        )

    def metadata_conflicts(self) -> tuple[MetadataConflict, ...]:
        synchronizer = self._cloud_synchronizer()
        if synchronizer is None:
            return ()
        outcome = synchronizer.synchronize()
        if outcome.imported_records:
            self._library_cache = None
            self.sync_metadata()
            return synchronizer.synchronize().conflicts
        return outcome.conflicts

    def resolve_metadata_conflict(self, record_id: str, choice: str) -> tuple[MetadataConflict, ...]:
        synchronizer = self._cloud_synchronizer()
        if synchronizer is None:
            raise LibraryWorkflowError("OneDrive JSON 미러 폴더가 설정되지 않았습니다.")
        outcome = synchronizer.resolve(record_id, choice)
        self._library_cache = None
        self.sync_metadata()
        return outcome.conflicts

    def _cloud_synchronizer(self) -> CloudMetadataSynchronizer | None:
        settings = self.settings()
        if not settings.metadata_sync_dir:
            return None
        _input_dir, root = self.configured_paths()
        return CloudMetadataSynchronizer(root, Path(settings.metadata_sync_dir))

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
        sync = self.sync_metadata()
        if sync.problems:
            current.setdefault("workflow", {})["metadata_sync_warning"] = "; ".join(
                sync.problems
            )
        self._library_cache = None
        return LibraryEntry(
            pdf_path=entry.pdf_path,
            sidecar_path=sidecar,
            metadata=metadata,
            work_id=entry.work_id,
            source_variant=entry.source_variant,
            record=current,
            sync_warning="; ".join(sync.problems),
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
            "last_edited_by": "user",
        },
        "provenance": {"extractor": "pymupdf", "ocr_used": False},
    }
    _apply_metadata(record, metadata)
    record["curation"]["field_sources"] = {
        "bibliography.title": "user",
        "bibliography.authors": "user",
        "bibliography.year": "user",
        "bibliography.venue": "user",
        "classification.category": "user",
        "classification.subcategory": "user",
        "classification.tags": "user",
        "description.summary_ko": "user",
    }
    return record
