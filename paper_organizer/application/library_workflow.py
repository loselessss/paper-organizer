"""Manual-first collection, duplicate review and library editing workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

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
from paper_organizer.application.summary_service import (
    PreparedSummary,
    SummaryExecution,
)
from paper_organizer.core.classifier import (
    TaxonomyError,
    classify_text,
    extract_venue,
    taxonomy_category_names,
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
    remove_search_entry,
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
DISCOVERY_OCR_PAGE_LIMIT = 5
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
    document_type: str = "paper"
    patent_office: str = ""
    publication_number: str = ""
    application_number: str = ""
    assignee: str = ""
    category: str = "Uncategorized"
    subcategory: str = "General"
    tags: list[str] = field(default_factory=list)
    summary: str = ""


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
    ocr_used: bool = field(default=False, repr=False, compare=False)
    ocr_complete: bool = field(default=False, repr=False, compare=False)


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
    kind: str = ""
    detection_status: str = ""
    detection_reason: str = ""
    estimated_title: str = ""
    duplicate_title: str = ""
    duplicate_kind: str = ""
    duplicate_score: float | None = None


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    pdf_path: Path
    sidecar_path: Path
    metadata: EditablePaperMetadata
    work_id: str
    source_variant: str
    record: dict[str, Any] = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class LibraryDeletionResult:
    deleted: int
    problems: tuple[str, ...] = ()


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


def _normalized_ai_tags(values: Iterable[Any]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = " ".join(str(value).split()).strip(" ,;#")
        key = tag.casefold()
        if not tag or len(tag) > 80 or key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) == 5:
            break
    return tags


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


def _discovery_ocr_cache_path(library_root: Path, file_sha256: str) -> Path:
    return library_root / "cache" / "ocr-discovery" / f"{file_sha256}.json"


def _load_discovery_ocr_cache(
    library_root: Path, file_sha256: str, page_count: int
) -> list[str] | None:
    path = _discovery_ocr_cache_path(library_root, file_sha256)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data.get("schema_version") != 1
            or data.get("file_sha256") != file_sha256
            or int(data.get("page_count", -1)) != page_count
            or not isinstance(data.get("pages"), list)
        ):
            return None
        texts = [""] * page_count
        for entry in data["pages"]:
            if not isinstance(entry, dict):
                continue
            index = int(entry.get("page", 0)) - 1
            text = entry.get("text")
            if 0 <= index < page_count and isinstance(text, str):
                texts[index] = text
        return texts
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_discovery_ocr_cache(
    library_root: Path, file_sha256: str, page_texts: list[str]
) -> None:
    _atomic_json_write(
        _discovery_ocr_cache_path(library_root, file_sha256),
        {
            "schema_version": 1,
            "file_sha256": file_sha256,
            "page_count": len(page_texts),
            "pages": [
                {"page": index, "text": text}
                for index, text in enumerate(page_texts, start=1)
                if text.strip()
            ],
            "updated_at": _now_iso(),
        },
    )


def _import_receipts_path(library_root: Path) -> Path:
    return library_root / "state" / "imported-sources.json"


def _ignored_file_ids_path(library_root: Path) -> Path:
    return library_root / "state" / "ignored-file-ids.json"


def _load_ignored_file_ids(library_root: Path) -> set[str]:
    path = _ignored_file_ids_path(library_root)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(value) for value in data.get("file_sha256", []) if value}
    except (OSError, TypeError, json.JSONDecodeError):
        return set()


def _record_ignored_file_id(library_root: Path, file_sha256: str) -> None:
    values = _load_ignored_file_ids(library_root)
    values.add(file_sha256)
    _atomic_json_write(
        _ignored_file_ids_path(library_root),
        {"schema_version": 1, "file_sha256": sorted(values), "updated_at": _now_iso()},
    )


def _forget_ignored_file_id(library_root: Path, file_sha256: str) -> None:
    values = _load_ignored_file_ids(library_root)
    if file_sha256 not in values:
        return
    values.remove(file_sha256)
    _atomic_json_write(
        _ignored_file_ids_path(library_root),
        {"schema_version": 1, "file_sha256": sorted(values), "updated_at": _now_iso()},
    )


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
    document = record.get("document", {})
    patent = record.get("patent", {})
    document_type = str(
        document.get("type")
        or record.get("detection", {}).get("document_type")
        or "paper"
    )
    raw_year = bibliography.get("year")
    try:
        year = int(raw_year) if raw_year not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    return EditablePaperMetadata(
        title=str(bibliography.get("title", "")),
        authors=[str(value) for value in bibliography.get("authors", [])],
        year=year,
        venue=(
            ""
            if document_type == "patent"
            else str(bibliography.get("venue", ""))
        ),
        document_type=document_type,
        patent_office=str(patent.get("office", "")),
        publication_number=str(patent.get("publication_number", "")),
        application_number=str(patent.get("application_number", "")),
        assignee=str(patent.get("assignee", "")),
        category=str(classification.get("category") or "Uncategorized"),
        subcategory=str(classification.get("subcategory") or "General"),
        tags=[str(value) for value in classification.get("tags", [])],
        summary=str(description.get("summary", "")),
    )


def _metadata_for_library_entry(
    record: dict[str, Any], sidecar_path: Path
) -> EditablePaperMetadata:
    metadata = _metadata_from_record(record)
    has_patent_details = any(
        (
            metadata.patent_office,
            metadata.publication_number,
            metadata.application_number,
            metadata.assignee,
        )
    )
    if metadata.document_type == "patent" and has_patent_details:
        return metadata
    if sidecar_path.suffix.casefold() != PAPERPACK_SUFFIX:
        return metadata
    try:
        pages = [text for _number, text in content_pages(load_paperpack_content(sidecar_path))]
        if metadata.document_type == "patent" or _detection(pages)[0] == "patent_likely":
            return _apply_patent_metadata(metadata, pages)
    except (OSError, PaperPackError, TypeError, ValueError):
        pass
    return metadata


def _default_metadata(path: Path, page_texts: list[str]) -> EditablePaperMetadata:
    pdf_title = ""
    pdf_author = ""
    visual_title = ""
    try:
        document = fitz.open(path)
        try:
            pdf_title = str(document.metadata.get("title") or "").strip()
            pdf_author = str(document.metadata.get("author") or "").strip()
            visual_title = _extract_visual_title(document)
        finally:
            document.close()
    except Exception:
        pass
    pdf_title = _repair_title_text(pdf_title)
    if not _is_usable_title(pdf_title):
        pdf_title = ""
    lines = [
        " ".join(line.split())
        for line in (page_texts[0].splitlines() if page_texts else [])
        if 5 <= len(" ".join(line.split())) <= 240
    ]
    title = pdf_title or visual_title or (lines[0] if lines else path.stem)
    authors = [value.strip() for value in re.split(r"[;,]", pdf_author) if value.strip()]
    if not authors:
        authors = _extract_first_page_byline(lines)
    beginning = " ".join(page_texts[:3])
    match = _YEAR_RE.search(beginning)
    return EditablePaperMetadata(
        title=title,
        authors=authors,
        year=int(match.group(0)) if match else None,
    )


def _repair_title_text(value: str) -> str:
    """Recover common UTF-8 and legacy Korean metadata mojibake."""

    original = " ".join(value.replace("\x00", " ").split())
    if not original:
        return ""
    utf8_candidates: list[str] = []
    for source_encoding in ("latin1", "cp1252"):
        try:
            encoded = original.encode(source_encoding)
        except UnicodeEncodeError:
            continue
        try:
            repaired = encoded.decode("utf-8")
        except UnicodeDecodeError:
            continue
        normalized = " ".join(repaired.replace("\x00", " ").split())
        if normalized:
            utf8_candidates.append(normalized)
    if utf8_candidates:
        return max(utf8_candidates, key=_title_text_quality)

    candidates = [original]
    latin1_count = sum("\u0080" <= character <= "\u00ff" for character in original)
    if latin1_count >= 2:
        try:
            repaired = original.encode("latin1").decode("cp949")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        else:
            normalized = " ".join(repaired.replace("\x00", " ").split())
            added_hangul = sum(
                "\uac00" <= character <= "\ud7a3" for character in normalized
            )
            if added_hangul >= 2:
                candidates.append(normalized)
    return max(candidates, key=_title_text_quality)


def _title_text_quality(value: str) -> int:
    alphanumeric = sum(character.isalnum() for character in value)
    hangul = sum("\uac00" <= character <= "\ud7a3" for character in value)
    controls = sum(
        ord(character) < 32 and character not in "\t\n\r" for character in value
    )
    private_or_replacement = sum(
        character == "\ufffd"
        or "\ue000" <= character <= "\uf8ff"
        for character in value
    )
    latin_mojibake_runs = sum(
        len(match.group(0))
        for match in re.finditer(r"[\u00c0-\u00ff]{2,}", value)
    )
    marker_penalty = sum(
        value.count(marker) for marker in ("Ã", "Â", "â€", "ðŸ")
    )
    return (
        alphanumeric * 2
        + hangul * 3
        + min(value.count(" "), 8)
        - controls * 40
        - private_or_replacement * 40
        - latin_mojibake_runs * 6
        - marker_penalty * 12
        - value.count("_") * 2
    )


def _is_usable_title(value: str) -> bool:
    if not 5 <= len(value) <= 240:
        return False
    if sum(character.isalnum() for character in value) < 3:
        return False
    if any(
        character == "\ufffd"
        or "\ue000" <= character <= "\uf8ff"
        or (ord(character) < 32 and character not in "\t\n\r")
        for character in value
    ):
        return False
    if re.search(r"[\u00c0-\u00ff]{3,}", value):
        return False
    folded = value.casefold().strip(" ._-")
    if re.fullmatch(
        r"(?:untitled|document|scan|제목\s*없음)(?:[\s._-]*\d+)?",
        folded,
    ):
        return False
    if re.fullmatch(
        r"\d+\s*호(?:[\s._-]*(?:최종|final|수정|완성))*",
        folded,
    ):
        return False
    return True


def _extract_visual_title(document: fitz.Document) -> str:
    """Return a prominent first-page heading when layout evidence is strong."""

    if document.page_count < 1:
        return ""
    try:
        page = document[0]
        raw = page.get_text("dict")
    except Exception:
        return ""
    candidates: list[tuple[float, float, str]] = []
    font_sizes: list[float] = []
    for block in raw.get("blocks", []):
        if not isinstance(block, dict) or block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = [
                span
                for span in line.get("spans", [])
                if isinstance(span, dict) and str(span.get("text") or "").strip()
            ]
            if not spans:
                continue
            font_sizes.extend(float(span.get("size") or 0) for span in spans)
            text = " ".join(
                str(span.get("text") or "").strip() for span in spans
            )
            text = " ".join(text.split())
            top = min(float(span.get("bbox", (0, 0, 0, 0))[1]) for span in spans)
            size = max(float(span.get("size") or 0) for span in spans)
            if top <= page.rect.height * 0.5 and _is_usable_title(text):
                candidates.append((size, top, text))
    positive_sizes = [size for size in font_sizes if size > 0]
    if not candidates or not positive_sizes:
        return ""
    body_size = statistics.median(positive_sizes)
    prominent = [
        candidate
        for candidate in candidates
        if candidate[0] >= max(body_size * 1.3, body_size + 2)
    ]
    if not prominent:
        return ""
    return max(prominent, key=lambda candidate: (candidate[0], -candidate[1]))[2]


def _extract_first_page_byline(lines: list[str]) -> list[str]:
    """Conservatively extract author names between the title and abstract."""

    excluded = (
        "abstract",
        "introduction",
        "review",
        "department",
        "university",
        "institute",
        "hospital",
        "corresponding",
        "received",
        "accepted",
        "doi",
        "http",
        "@",
    )
    initial_name = re.compile(
        r"(?:^|,\s*|\band\s+)(?:[A-Z]\.\s*)+[A-Z][A-Za-z'’-]+(?:\s|$)"
    )
    for line in lines[1:10]:
        folded = line.casefold()
        if folded.startswith(("abstract", "introduction")):
            break
        if any(marker in folded for marker in excluded):
            continue
        if not initial_name.search(line):
            continue
        values = [
            value.strip()
            for value in re.split(r"\s*;\s*|\s+\band\b\s+|,\s*(?=[A-Z]\.)", line)
            if value.strip()
        ]
        if values:
            return values
    return []


def _detection(page_texts: list[str]) -> tuple[str, str]:
    text = " ".join(page_texts).casefold()
    if len(text.strip()) < 500:
        return "needs_ocr", "추출된 본문이 너무 적어 OCR 또는 수동 확인이 필요합니다."
    patent_markers = [
        marker
        for marker in (
            "patent",
            "publication number",
            "application number",
            "inventor",
            "applicant",
            "claims",
            "청구항",
            "발명자",
            "출원번호",
            "공개번호",
        )
        if marker in text
    ]
    if len(patent_markers) >= 2:
        return "patent_likely", f"특허 문서 표식 확인: {', '.join(patent_markers)}"
    markers = [marker for marker in ("abstract", "introduction", "references", "doi") if marker in text]
    if len(markers) >= 2:
        return "academic_likely", f"학술 문서 표식 확인: {', '.join(markers)}"
    return "needs_review", "학술 논문 구조가 충분히 확인되지 않아 사용자 검토가 필요합니다."


def _is_supported_document(status: str) -> bool:
    return status in {"academic_likely", "patent_likely"}


def _apply_patent_metadata(
    metadata: EditablePaperMetadata, page_texts: list[str]
) -> EditablePaperMetadata:
    text = "\n".join(page_texts[:5])
    metadata.document_type = "patent"
    metadata.venue = ""
    patterns = {
        "publication_number": (
            r"(?im)^\s*(?:publication\s+(?:number|no\.?)|공개번호)\s*[:#]?\s*"
            r"([A-Z]{0,3}\s*\d[\w .\-/]{3,30})"
        ),
        "application_number": (
            r"(?im)^\s*(?:application\s+(?:number|no\.?)|출원번호)\s*[:#]?\s*"
            r"([A-Z]{0,3}\s*\d[\w .\-/]{3,30})"
        ),
        "assignee": (
            r"(?im)^\s*(?:applicant|assignee|출원인|특허권자)\s*[:#]?\s*(.{2,120})$"
        ),
    }
    for field_name, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            setattr(metadata, field_name, " ".join(match.group(1).split()).strip(" ;,"))
    inventor = re.search(
        r"(?im)^\s*(?:inventors?|발명자)\s*[:#]?\s*(.{2,240})$", text
    )
    if inventor:
        metadata.authors = [
            value.strip()
            for value in re.split(r"\s*;\s*|,\s*(?=[A-Z][a-z])", inventor.group(1))
            if value.strip()
        ]
    number = metadata.publication_number.upper().replace(" ", "")
    office_names = {
        "US": "USPTO",
        "EP": "EPO",
        "WO": "WIPO",
        "KR": "KIPO",
        "JP": "JPO",
        "CN": "CNIPA",
    }
    metadata.patent_office = next(
        (office for prefix, office in office_names.items() if number.startswith(prefix)),
        "",
    )
    return metadata


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
        self._trackers: dict[Path, DiscoveryTracker] = {}
        self._cache: dict[Path, tuple[int, int, ReviewItem]] = {}
        self._short_documents: dict[Path, tuple[int, int]] = {}
        self._library_cache: list[LibraryEntry] | None = None

    def settings(self) -> AppSettings:
        return load_settings(self._settings_path)

    def configured_paths(self) -> tuple[Path, Path]:
        settings = self.settings()
        inputs = self.configured_input_dirs()
        return (
            inputs[0],
            Path(settings.library_root) if settings.library_root else default_library_root(),
        )

    def configured_input_dirs(self) -> tuple[Path, ...]:
        settings = self.settings()
        raw = settings.watch_folders or (
            [settings.input_dir] if settings.input_dir else [str(default_input_dir())]
        )
        return tuple(Path(value).expanduser().resolve() for value in raw)

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
        research_categories: list[str] | None = None,
        focus_categories: list[str] | None = None,
        watch_folders: list[Path] | None = None,
    ) -> AppSettings:
        requested_inputs = watch_folders if watch_folders is not None else [input_dir]
        input_paths: list[Path] = []
        seen_inputs: set[str] = set()
        for value in requested_inputs:
            path = value.expanduser().resolve()
            key = os.path.normcase(str(path))
            if key in seen_inputs:
                continue
            seen_inputs.add(key)
            input_paths.append(path)
        if not input_paths:
            raise LibraryWorkflowError("감시 폴더를 하나 이상 지정하세요.")
        input_path = input_paths[0]
        library_path = library_root.expanduser().resolve()
        missing = [path for path in input_paths if not path.is_dir()]
        if missing:
            raise LibraryWorkflowError(f"감시 폴더가 존재하지 않습니다: {missing[0]}")
        if any(path == library_path for path in input_paths):
            raise LibraryWorkflowError("감시 폴더와 라이브러리 폴더는 달라야 합니다.")
        settings = self.settings()
        previous_library = (
            Path(settings.library_root).expanduser().resolve()
            if settings.library_root
            else None
        )
        if (
            previous_library is not None
            and previous_library != library_path
            and previous_library.is_dir()
        ):
            library_path.mkdir(parents=True, exist_ok=True)
            if any(library_path.iterdir()):
                raise LibraryWorkflowError(
                    "새 라이브러리 폴더가 비어 있지 않아 기존 데이터를 옮길 수 없습니다."
                )
            try:
                for child in previous_library.iterdir():
                    shutil.move(str(child), str(library_path / child.name))
            except Exception as exc:
                raise LibraryWorkflowError(
                    f"라이브러리 데이터 이동 중 실패했습니다: {exc}"
                ) from None
        else:
            library_path.mkdir(parents=True, exist_ok=True)
        settings.input_dir = str(input_path)
        settings.watch_folders = [str(path) for path in input_paths]
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
        if research_categories is not None:
            settings.research_categories = [
                name.strip() for name in research_categories if name.strip()
            ]
        if focus_categories is not None:
            settings.focus_categories = [
                name.strip() for name in focus_categories if name.strip()
            ]
        save_settings(settings, self._settings_path)
        self._library_cache = None
        return settings

    def scan(self, progress: Callable[[str], None] | None = None) -> ReviewScan:
        settings = self.settings()
        input_dirs = self.configured_input_dirs()
        library_root = self.configured_paths()[1]
        receipts = _load_import_receipts(library_root)
        problems: list[ScanProblem] = []
        candidates: list[Path] = []
        stable = []
        for input_dir in input_dirs:
            if not input_dir.is_dir():
                problems.append(
                    ScanProblem(input_dir, "감시 폴더가 없거나 접근할 수 없습니다.")
                )
                continue
            candidates.extend(iter_pdf_candidates(input_dir))
            tracker = self._trackers.setdefault(input_dir, DiscoveryTracker())
            stable.extend(
                tracker.scan(
                    input_dir,
                    minimum_age_seconds=settings.minimum_age_seconds,
                )
            )
        active_candidates: list[Path] = []
        for path in candidates:
            try:
                stat = path.stat()
            except OSError:
                continue
            short_signature = self._short_documents.get(path)
            if short_signature == (stat.st_size, stat.st_mtime_ns):
                continue
            if short_signature is not None:
                self._short_documents.pop(path, None)
            if not _source_is_already_imported(
                path, stat.st_size, stat.st_mtime_ns, library_root, receipts
            ):
                active_candidates.append(path)
        active_set = set(active_candidates)
        stable = [found for found in stable if found.path in active_set]
        references = tuple(_library_references(library_root)) if library_root.is_dir() else ()
        ignored_file_ids = _load_ignored_file_ids(library_root)
        items: list[ReviewItem] = []
        seen_file_ids: set[str] = set()
        for found in stable:
            cached = self._cache.get(found.path)
            key = (found.observation.size, found.observation.modified_ns)
            if cached and cached[:2] == key:
                items.append(cached[2])
                continue
            try:
                if progress is not None:
                    progress(f"{found.path.name}: PDF 본문 확인 중")
                page_texts = extract_page_texts(found.path)
                if len(page_texts) < 3:
                    self._short_documents[found.path] = key
                    self._forget_discovery(found.path)
                    self._cache.pop(found.path, None)
                    continue
                file_sha256 = sha256_file(found.path)
                if file_sha256 in ignored_file_ids:
                    self._forget_discovery(found.path)
                    continue
                if file_sha256 in seen_file_ids:
                    self._forget_discovery(found.path)
                    continue
                seen_file_ids.add(file_sha256)
                ocr_used = False
                ocr_complete = False
                if _detection(page_texts)[0] == "needs_ocr":
                    try:
                        from paper_organizer.application.background_ocr import (
                            ocr_page_texts,
                        )

                        ocr_indexes = tuple(
                            range(min(len(page_texts), DISCOVERY_OCR_PAGE_LIMIT))
                        )
                        recognized = _load_discovery_ocr_cache(
                            library_root, file_sha256, len(page_texts)
                        )
                        if recognized is None:
                            if progress is not None:
                                progress(
                                    f"{found.path.name}: 빠른 OCR 준비 "
                                    f"(앞 {len(ocr_indexes)}페이지)"
                                )
                            recognized = ocr_page_texts(
                                found.path,
                                page_indexes=ocr_indexes,
                                background=True,
                                progress=(
                                    lambda done, total, name=found.path.name: progress(
                                        f"{name}: 빠른 OCR {done}/{total}페이지"
                                    )
                                    if progress is not None
                                    else None
                                ),
                            )
                            _save_discovery_ocr_cache(
                                library_root, file_sha256, recognized
                            )
                        if sum(len(text.strip()) for text in recognized) >= 500:
                            page_texts = [
                                ocr_text if ocr_text.strip() else native_text
                                for native_text, ocr_text in zip(page_texts, recognized)
                            ]
                            ocr_used = True
                            ocr_complete = len(ocr_indexes) == len(page_texts)
                    except Exception as exc:
                        problems.append(
                            ScanProblem(found.path, f"빠른 OCR 실패: {exc}")
                        )
                identity = build_identity_from_pages(file_sha256, page_texts)
                status, reason = _detection(page_texts)
                metadata = _default_metadata(found.path, page_texts)
                if status == "patent_likely":
                    metadata = _apply_patent_metadata(metadata, page_texts)
                item = ReviewItem(
                    path=found.path,
                    identity=identity,
                    metadata=metadata,
                    detection_status=status,
                    detection_reason=reason,
                    duplicate=_best_duplicate(identity, references),
                    page_texts=tuple(page_texts),
                    ocr_used=ocr_used,
                    ocr_complete=ocr_complete,
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

    def _forget_discovery(self, path: Path) -> None:
        for tracker in self._trackers.values():
            tracker.forget(path)

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
            if not _is_supported_document(item.detection_status) or item.duplicate is not None:
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
            document_type=item.metadata.document_type,
            patent_office=item.metadata.patent_office,
            publication_number=item.metadata.publication_number,
            application_number=item.metadata.application_number,
            assignee=item.metadata.assignee,
            category=item.metadata.category,
            subcategory=item.metadata.subcategory,
            tags=list(item.metadata.tags),
            summary=item.metadata.summary,
        )
        page_texts = list(item.page_texts)
        if not page_texts:
            return metadata
        settings = self.settings()
        try:
            result = classify_text(
                metadata.title,
                page_texts,
                allowed_categories=(
                    settings.focus_categories
                    or settings.research_categories
                    or None
                ),
            )
        except TaxonomyError:
            result = None
        if result is not None and result.classified:
            metadata.category = result.category
            metadata.subcategory = result.subcategory
        if metadata.document_type == "patent":
            metadata.venue = ""
        elif not metadata.venue:
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
        content = build_content_payload(
            page_texts,
            ocr_used=item.ocr_used,
            ocr_complete=item.ocr_complete,
        )
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
        self._forget_discovery(source)
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
        """Move a new PDF to recoverable app trash and remember its file ID."""
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
                    "kind": (
                        "unorganized_duplicate"
                        if item.duplicate is not None and item.duplicate.confirmed
                        else "discarded_new_pdf"
                    ),
                    "created_at": _now_iso(),
                    "original_path": str(source),
                    "trashed_name": destination.name,
                    "sha256": item.identity.file_sha256,
                    "duplicate_of": (
                        str(item.duplicate.pdf_path) if item.duplicate is not None else ""
                    ),
                    "detection_status": item.detection_status,
                    "detection_reason": item.detection_reason,
                    "estimated_title": item.metadata.title,
                    "duplicate_title": (
                        item.duplicate.title if item.duplicate is not None else ""
                    ),
                    "duplicate_kind": (
                        item.duplicate.match.kind.value
                        if item.duplicate is not None
                        else ""
                    ),
                    "duplicate_score": (
                        item.duplicate.match.score
                        if item.duplicate is not None
                        else None
                    ),
                    "restored_at": None,
                },
            )
        except Exception as exc:
            if moved and destination.exists() and not source.exists():
                shutil.move(str(destination), str(source))
            raise LibraryWorkflowError(f"제외 목록 이동을 완료하지 못했습니다: {exc}") from None
        self._forget_discovery(source)
        self._cache.pop(source, None)
        _record_ignored_file_id(library_root, item.identity.file_sha256)
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
                        kind=str(data.get("kind", "")),
                        detection_status=str(data.get("detection_status", "")),
                        detection_reason=str(data.get("detection_reason", "")),
                        estimated_title=str(data.get("estimated_title", "")),
                        duplicate_title=str(data.get("duplicate_title", "")),
                        duplicate_kind=str(data.get("duplicate_kind", "")),
                        duplicate_score=(
                            float(data["duplicate_score"])
                            if data.get("duplicate_score") is not None
                            else None
                        ),
                    )
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(entries, key=lambda entry: entry.operation_id, reverse=True)

    def restore_trash(self, entry: TrashEntry) -> Path:
        input_dir, root = self.configured_paths()
        manifest = entry.manifest_path.resolve()
        if not _inside((root / "trash").resolve(), manifest):
            raise LibraryWorkflowError("제외 목록 밖의 작업은 복원할 수 없습니다.")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("restored_at"):
            raise LibraryWorkflowError("이미 복원된 작업입니다.")
        trashed = manifest.parent / str(data["trashed_name"])
        if not trashed.is_file() or sha256_file(trashed) != str(data["sha256"]):
            raise LibraryWorkflowError("제외 파일이 없거나 내용이 바뀌었습니다.")
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
        _forget_ignored_file_id(root, str(data.get("sha256", "")))
        data["restored_at"] = _now_iso()
        data["restored_path"] = str(destination)
        try:
            _atomic_json_write(manifest, data)
        except Exception as exc:
            if destination.exists() and not trashed.exists():
                shutil.move(str(destination), str(trashed))
            raise LibraryWorkflowError(f"복원 기록을 저장하지 못했습니다: {exc}") from None
        self._forget_discovery(destination)
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

    def apply_analysis_failure(
        self,
        source_path: Path,
        prepared: PreparedSummary,
        message: str,
        *,
        error_type: str = "",
        failure_kind: str = "provider_or_application",
        request_attempts: int | None = None,
    ) -> None:
        """Persist failure state and deterministic excerpts without faking an AI result."""

        _input_dir, root = self.configured_paths()
        source = source_path.expanduser().resolve()
        papers_root = (root / "papers").resolve()
        if (
            source.suffix.casefold() != PAPERPACK_SUFFIX
            or not source.is_file()
            or not _inside(papers_root, source)
        ):
            raise LibraryWorkflowError(
                "분석 실패 정보는 라이브러리의 paperpack에만 저장할 수 있습니다."
            )
        record = load_paperpack_metadata(source)
        now = _now_iso()
        safe_message = " ".join(str(message).split())[:500] or "알 수 없는 분석 오류"
        fallback = prepared.regex_fallback
        fallback_record = {
            "source": "auto:regex",
            "abstract": fallback.abstract,
            "abstract_pdf_pages": list(fallback.abstract_pdf_pages),
            "facts": list(fallback.facts),
        }
        allowed_kinds = {
            "json_validation",
            "language_validation",
            "timeout",
            "authentication",
            "ollama_runtime",
            "provider_or_application",
        }
        diagnostics: dict[str, Any] = {
            "stage": "summary_generation_and_validation",
            "failure_kind": (
                failure_kind
                if failure_kind in allowed_kinds
                else "provider_or_application"
            ),
            "error_type": re.sub(r"[^A-Za-z0-9_.]", "", error_type)[:120],
            "provider": prepared.preview.provider,
            "model": prepared.preview.model,
            "analysis_level": prepared.preview.mode.value,
            "summary_strategy": prepared.preview.summary_strategy,
            "output_language": prepared.preview.output_language,
            "included_sections": list(prepared.preview.included_sections),
        }
        if (
            isinstance(request_attempts, int)
            and not isinstance(request_attempts, bool)
            and 1 <= request_attempts <= 10
        ):
            diagnostics["request_attempts"] = request_attempts
        failed_attempt = {
            "status": "failed",
            "error": safe_message,
            "failed_at": now,
            "diagnostics": diagnostics,
            "fallback": fallback_record,
        }
        analysis = record.get("analysis")
        if isinstance(analysis, dict) and analysis.get("status") == "completed":
            analysis["last_attempt"] = failed_attempt
            analysis["fallback"] = fallback_record
        else:
            record["analysis"] = failed_attempt
        workflow = record.setdefault("workflow", {})
        workflow.update(
            {
                "analysis_status": "failed",
                "needs_reanalysis": True,
                "updated_at": now,
            }
        )
        try:
            update_paperpack(source, record, changed_by="auto:regex")
            rebuild_library_index(root)
        except (OSError, PaperPackError) as exc:
            raise LibraryWorkflowError(
                f"분석 실패 정보와 정규식 추출본을 저장하지 못했습니다: {exc}"
            ) from None
        self._index_search_entry(source)
        self._library_cache = None

    def retry_queue_item(self, queue_id: str, *, high: bool = False) -> AnalysisQueueItem:
        return self._queue().retry(queue_id, high=high)

    def queue_reanalysis(
        self, entries: Iterable[LibraryEntry], *, high: bool = False
    ) -> tuple[int, tuple[str, ...]]:
        """Queue stored papers for a fresh AI result without deleting the old one."""

        queue = self._queue()
        queued = 0
        problems: list[str] = []
        current = {item.queue_id: item for item in queue.load()}
        for entry in entries:
            title = entry.metadata.title or entry.sidecar_path.stem
            file_sha256 = str(entry.record.get("file", {}).get("sha256") or "").strip()
            if not file_sha256 or not entry.sidecar_path.is_file():
                problems.append(f"{title}: PaperPack 또는 파일 식별자가 없습니다.")
                continue
            queue_id = f"sha256:{file_sha256}"
            existing = current.get(queue_id)
            if existing is not None and existing.status == "analyzing":
                problems.append(f"{title}: 현재 분석 중입니다.")
                continue
            try:
                item = queue.relocate(
                    file_sha256,
                    entry.sidecar_path,
                    status="organized_pending_analysis",
                    title=title,
                )
                if high:
                    item = queue.set_priority(item.queue_id, True)
                current[item.queue_id] = item
                queued += 1
            except (OSError, AnalysisQueueError) as exc:
                problems.append(f"{title}: {exc}")
        return queued, tuple(problems)

    def approve_category_suggestion(self, entry: LibraryEntry) -> str:
        """Save an AI-proposed category only after explicit user approval."""

        suggestion = str(
            entry.record.get("analysis", {}).get("suggested_category") or ""
        ).strip()
        if not suggestion:
            raise LibraryWorkflowError("승인할 추천 연구분야가 없습니다.")
        if len(suggestion) > 80 or "," in suggestion:
            raise LibraryWorkflowError("추천 연구분야 이름이 올바르지 않습니다.")
        settings = self.settings()
        categories = list(settings.research_categories)
        if not categories:
            try:
                categories = list(taxonomy_category_names())
            except TaxonomyError:
                categories = []
        existing = {name.casefold() for name in categories}
        if suggestion.casefold() not in existing:
            categories.append(suggestion)
        settings.research_categories = categories
        if settings.focus_categories and suggestion.casefold() not in {
            name.casefold() for name in settings.focus_categories
        }:
            settings.focus_categories.append(suggestion)
        save_settings(settings, self._settings_path)
        return suggestion

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
        inferred_metadata = _metadata_for_library_entry(record, source)
        if inferred_metadata.document_type == "patent":
            _apply_metadata(record, inferred_metadata)
        now = _now_iso()
        result = execution.result
        data = result.data
        ai_tags = _normalized_ai_tags(data.meta_tags)
        suggested_category = (
            data.suggested_category.strip() if not data.category.strip() else ""
        )
        record["analysis"] = {
            "status": "completed",
            "analysis_level": execution.preview.mode.value,
            "summary": data.summary,
            "research_question": data.research_question,
            "methods": list(data.methods),
            "contributions": list(data.contributions),
            "limitations": list(data.limitations),
            "keywords": list(data.keywords),
            "meta_tags": ai_tags,
            "suggested_category": suggested_category,
            "completed_at": now,
            "provenance": execution.provenance,
        }
        description = record.setdefault("description", {})
        classification = record.setdefault("classification", {})
        classification["ai_tags"] = ai_tags
        curation = record.setdefault("curation", {})
        locked = set(curation.get("locked_fields", []))
        sources = curation.setdefault("field_sources", {})
        values = {
            "summary": data.summary,
            "research_question": data.research_question,
            "methods": list(data.methods),
            "keywords": list(data.keywords),
        }
        if execution.preview.summary_strategy != "hierarchical":
            values["contributions"] = list(data.contributions)
            values["limitations"] = list(data.limitations)
        for name, value in values.items():
            field = f"description.{name}"
            current_source = str(sources.get(field) or "")
            current = description.get(name)
            if field in locked or current_source == "user":
                continue
            if current and not current_source.startswith("ai:"):
                continue
            description[name] = value
            sources[field] = f"ai:{result.provider}"
        sources["classification.ai_tags"] = f"ai:{result.provider}"
        self._apply_ai_bibliography(record, data, f"ai:{result.provider}")
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
        moved = self._relocate_for_classification(source, record)
        self._index_search_entry(moved)
        self._library_cache = None

    @staticmethod
    def _apply_ai_bibliography(
        record: dict[str, Any], data: Any, source_label: str
    ) -> None:
        """Overwrite only fields the user has not curated or locked.

        field_sources가 "user"인 필드는 사람이 고친 값이므로 건드리지 않고,
        정규식 1차 분류(auto:regex)나 빈 값만 AI 결과로 채운다.
        """

        curation = record.setdefault("curation", {})
        locked = set(curation.get("locked_fields", []))
        sources = curation.setdefault("field_sources", {})
        bibliography = record.setdefault("bibliography", {})
        classification = record.setdefault("classification", {})
        is_patent = (
            record.get("document", {}).get("type") == "patent"
            or record.get("detection", {}).get("document_type") == "patent"
        )
        if is_patent:
            bibliography["venue"] = ""

        def replaceable(field: str, current: Any) -> bool:
            if field in locked or sources.get(field) == "user":
                return False
            current_source = str(sources.get(field) or "")
            return (
                not current
                or current_source == "auto:regex"
                or current_source.startswith("ai:")
            )

        year: int | None = None
        if data.year.strip().isdigit() and len(data.year.strip()) == 4:
            year = int(data.year.strip())
        candidates = [
            (bibliography, "bibliography.title", "title", data.title.strip()),
            (
                bibliography,
                "bibliography.authors",
                "authors",
                [value.strip() for value in data.authors if value.strip()],
            ),
            (bibliography, "bibliography.year", "year", year),
            (
                bibliography,
                "bibliography.venue",
                "venue",
                "" if is_patent else data.venue.strip(),
            ),
            (
                classification,
                "classification.category",
                "category",
                data.category.strip(),
            ),
            (
                classification,
                "classification.subcategory",
                "subcategory",
                data.subcategory.strip(),
            ),
        ]
        for target, field, key, value in candidates:
            if not value:
                continue
            if not replaceable(field, target.get(key)):
                continue
            target[key] = value
            sources[field] = source_label

    def _relocate_for_classification(
        self, paperpack: Path, record: dict[str, Any]
    ) -> Path:
        """Move a paperpack when AI changed its category, or leave it in place.

        이동에 실패하면 기존 위치를 그대로 유지한다. 분류는 메타데이터가
        기준이고 폴더는 그 사본이므로, 실패해도 데이터를 잃지 않는다.
        """

        _input_dir, root = self.configured_paths()
        classification = record.get("classification", {})
        raw_category = str(classification.get("category") or "").strip()
        if not raw_category:
            return paperpack
        category = _safe_component(raw_category, "Uncategorized")
        subcategory = _safe_component(
            str(classification.get("subcategory") or ""), "General"
        )
        destination_dir = root / "papers" / category / subcategory
        if paperpack.parent.resolve() == destination_dir.resolve():
            return paperpack
        try:
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = _unique_paperpack_destination(destination_dir, paperpack.name)
            shutil.move(str(paperpack), str(destination))
        except OSError:
            return paperpack
        try:
            record["file"]["relative_path"] = destination.relative_to(root).as_posix()
            record["file"]["current_name"] = destination.name
            update_paperpack(destination, record, changed_by="relocate")
            self._queue().relocate(
                str(record.get("file", {}).get("sha256") or ""),
                destination,
                status="completed",
                title=str(record.get("bibliography", {}).get("title") or destination.stem),
            )
            rebuild_library_index(root)
        except (OSError, KeyError, AnalysisQueueError, PaperPackError):
            pass
        try:
            remove_search_entry(root, str(record.get("id") or ""))
        except (OSError, SearchIndexError):
            pass
        return destination

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

    def paperpack_needs_ocr(self, path: Path) -> bool:
        source = path.expanduser().resolve()
        if source.suffix.casefold() != PAPERPACK_SUFFIX or not source.is_file():
            return False
        try:
            content = load_paperpack_content(source)
            page_count = int(content.get("page_count", 0))
            if page_count <= 0:
                record = load_paperpack_metadata(source)
                page_count = int(record.get("file", {}).get("page_count", 0))
            if page_count < 2:
                return False
            status = str(content.get("ocr_status") or "")
            if status == "partial":
                return True
            if status == "complete" or content.get("ocr_used"):
                return False
            return int(content.get("character_count", 0)) < 500
        except (OSError, TypeError, ValueError, PaperPackError):
            return False

    def complete_paperpack_ocr(
        self,
        path: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[str]:
        """Complete full-document OCR and atomically persist it in the PaperPack."""

        source = path.expanduser().resolve()
        if source.suffix.casefold() != PAPERPACK_SUFFIX or not source.is_file():
            raise LibraryWorkflowError("전체 OCR 대상 PaperPack을 찾을 수 없습니다.")
        try:
            content = load_paperpack_content(source)
            pdf_path = self.materialize_pdf(source)
            from paper_organizer.application.background_ocr import ocr_page_texts

            recognized = ocr_page_texts(
                pdf_path,
                progress=progress,
                background=True,
            )
            if len(recognized) < 2:
                raise LibraryWorkflowError(
                    "2페이지 미만 문서는 OCR 대상에서 제외됩니다."
                )
            existing = [""] * len(recognized)
            raw_pages = content.get("pages", [])
            if isinstance(raw_pages, list):
                for fallback, entry in enumerate(raw_pages, start=1):
                    if not isinstance(entry, dict):
                        continue
                    try:
                        index = int(entry.get("page", fallback)) - 1
                    except (TypeError, ValueError):
                        continue
                    text = entry.get("text")
                    if 0 <= index < len(existing) and isinstance(text, str):
                        existing[index] = text
            merged = [
                ocr_text if ocr_text.strip() else native_text
                for native_text, ocr_text in zip(existing, recognized)
            ]
            if sum(len(text.strip()) for text in merged) < 500:
                raise LibraryWorkflowError(
                    "내장 OCR을 완료했지만 인식된 본문이 너무 적습니다."
                )
            record = load_paperpack_metadata(source)
            record.setdefault("provenance", {}).update(
                {
                    "ocr_used": True,
                    "ocr_completed_at": _now_iso(),
                    "extractor": "rapidocr",
                }
            )
            payload = build_content_payload(
                merged,
                extractor="rapidocr",
                ocr_used=True,
                ocr_complete=True,
            )
            update_paperpack(
                source,
                record,
                content=payload,
                changed_by="background-ocr",
            )
            self._index_search_entry(source)
            self._library_cache = None
            return merged
        except LibraryWorkflowError:
            raise
        except Exception as exc:
            raise LibraryWorkflowError(f"전체 OCR을 저장하지 못했습니다: {exc}") from None

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

    def remove_completed_from_queue(self) -> int:
        return self._queue().remove_completed()

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
                            metadata=_metadata_for_library_entry(record, sidecar),
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
                    metadata.patent_office,
                    metadata.publication_number,
                    metadata.application_number,
                    metadata.assignee,
                    metadata.category,
                    metadata.subcategory,
                    *metadata.tags,
                    *[
                        str(value)
                        for value in entry.record.get("classification", {}).get(
                            "ai_tags", []
                        )
                    ],
                    metadata.summary,
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

    def permanently_delete_library_entries(
        self, entries: Iterable[LibraryEntry]
    ) -> LibraryDeletionResult:
        """Permanently remove approved library files and their derived state."""

        selected = list(entries)
        if not selected:
            raise LibraryWorkflowError("완전 삭제할 라이브러리 항목을 선택하세요.")
        _input_dir, root = self.configured_paths()
        papers_root = (root / "papers").resolve()
        queue = self._queue()
        queue_by_id = {item.queue_id: item for item in queue.load()}
        plans: list[
            tuple[
                LibraryEntry,
                tuple[Path, ...],
                str,
                str,
            ]
        ] = []
        seen: set[Path] = set()
        for entry in selected:
            sidecar = entry.sidecar_path.expanduser().resolve()
            if sidecar in seen:
                raise LibraryWorkflowError("같은 라이브러리 항목이 중복 선택되었습니다.")
            seen.add(sidecar)
            if not _inside(papers_root, sidecar):
                raise LibraryWorkflowError("라이브러리 밖의 파일은 삭제할 수 없습니다.")
            is_paperpack = sidecar.suffix.casefold() == PAPERPACK_SUFFIX
            is_legacy_sidecar = sidecar.name.endswith(SIDECAR_SUFFIX)
            if not sidecar.is_file() or not (is_paperpack or is_legacy_sidecar):
                raise LibraryWorkflowError(
                    f"삭제할 PaperPack 또는 색인 파일을 찾을 수 없습니다: {sidecar.name}"
                )
            try:
                record = load_record(sidecar)
                identity = _identity_from_record(record)
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                PaperPackError,
            ) as exc:
                raise LibraryWorkflowError(
                    f"삭제 대상을 검증할 수 없습니다: {sidecar.name}: {exc}"
                ) from None
            queue_id = f"sha256:{identity.file_sha256}"
            queue_item = queue_by_id.get(queue_id)
            if queue_item is not None and queue_item.status == "analyzing":
                raise LibraryWorkflowError(
                    f"현재 분석 중인 항목은 삭제할 수 없습니다: "
                    f"{entry.metadata.title or sidecar.stem}"
                )
            targets = [sidecar]
            if is_legacy_sidecar:
                pdf_path = entry.pdf_path.expanduser().resolve()
                targets.extend(
                    (
                        pdf_path,
                        Path(f"{pdf_path}.content.json"),
                        Path(
                            str(sidecar)[: -len(SIDECAR_SUFFIX)]
                            + ".content.json"
                        ),
                    )
                )
            unique_targets: list[Path] = []
            for target in targets:
                target = target.resolve()
                if target in unique_targets or not target.exists():
                    continue
                if not _inside(papers_root, target):
                    raise LibraryWorkflowError(
                        f"라이브러리 밖의 연관 파일은 삭제할 수 없습니다: {target.name}"
                    )
                unique_targets.append(target)
            plans.append(
                (
                    entry,
                    tuple(unique_targets),
                    identity.file_sha256,
                    identity.file_id,
                )
            )

        deleted = 0
        problems: list[str] = []
        for entry, targets, file_sha256, file_id in plans:
            title = entry.metadata.title or entry.sidecar_path.stem
            queue_id = f"sha256:{file_sha256}"
            current = next(
                (item for item in queue.load() if item.queue_id == queue_id),
                None,
            )
            if current is not None and current.status == "analyzing":
                problems.append(f"{title}: 분석이 시작되어 삭제하지 않았습니다.")
                continue
            try:
                if entry.sidecar_path.suffix.casefold() == PAPERPACK_SUFFIX:
                    _source, _workspace, edit_state = self._paperpack_edit_paths(
                        entry.sidecar_path
                    )
                    self.discard_paperpack_working_copy(entry.sidecar_path)
                    try:
                        edit_state.parent.rmdir()
                    except OSError:
                        pass
                for target in targets:
                    target.unlink()
            except (OSError, LibraryWorkflowError) as exc:
                problems.append(f"{title}: 파일 삭제 실패: {exc}")
                continue
            deleted += 1
            try:
                if current is not None:
                    queue.remove(queue_id)
            except (OSError, AnalysisQueueError) as exc:
                problems.append(f"{title}: 분석 큐 정리 실패: {exc}")
            try:
                remove_search_entry(root, file_id)
            except (OSError, SearchIndexError) as exc:
                problems.append(f"{title}: 검색 색인 정리 실패: {exc}")
            for cached in (
                root / "cache" / "pdf" / f"{file_sha256}.pdf",
                _discovery_ocr_cache_path(root, file_sha256),
            ):
                try:
                    cached.unlink(missing_ok=True)
                except OSError as exc:
                    problems.append(f"{title}: 임시 파일 정리 실패: {exc}")
            history_root = (root / "history").resolve()
            history = (
                history_root / _safe_component(file_sha256, "unknown")
            ).resolve()
            if (
                file_sha256
                and history.is_dir()
                and _inside(history_root, history)
            ):
                try:
                    shutil.rmtree(history)
                except OSError as exc:
                    problems.append(f"{title}: 편집 이력 정리 실패: {exc}")
            parent = entry.sidecar_path.resolve().parent
            while parent != papers_root and _inside(papers_root, parent):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

        if deleted:
            try:
                rebuild_library_index(root)
            except Exception as exc:
                problems.append(f"통합 색인 재생성 실패: {exc}")
        self._library_cache = None
        return LibraryDeletionResult(deleted, tuple(problems))

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
                    "document.type": "user",
                    "patent.office": "user",
                    "patent.publication_number": "user",
                    "patent.application_number": "user",
                    "patent.assignee": "user",
                    "classification.category": "user",
                    "classification.subcategory": "user",
                    "classification.tags": "user",
                    "description.summary": "user",
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
    document_type = "patent" if metadata.document_type == "patent" else "paper"
    record.setdefault("document", {})["type"] = document_type
    record.setdefault("bibliography", {}).update(
        {
            "title": metadata.title.strip(),
            "authors": metadata.authors,
            "year": metadata.year,
            "venue": "" if document_type == "patent" else metadata.venue.strip(),
        }
    )
    if document_type == "patent":
        record["patent"] = {
            "office": metadata.patent_office.strip(),
            "publication_number": metadata.publication_number.strip(),
            "application_number": metadata.application_number.strip(),
            "assignee": metadata.assignee.strip(),
        }
    record.setdefault("classification", {}).update(
        {
            "category": metadata.category.strip() or "Uncategorized",
            "subcategory": metadata.subcategory.strip() or "General",
            "tags": metadata.tags,
        }
    )
    record.setdefault("description", {})["summary"] = metadata.summary.strip()


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
            "is_patent": item.detection_status == "patent_likely",
            "document_type": (
                "patent" if item.detection_status == "patent_likely" else "paper"
            ),
            "confidence": 0.85 if _is_supported_document(item.detection_status) else 0.0,
            "reason": item.detection_reason,
        },
        "workflow": {
            "status": "organized",
            "needs_review": not _is_supported_document(item.detection_status),
            "review_reason": "" if _is_supported_document(item.detection_status) else item.detection_reason,
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
            "description.summary",
        )
    }
    if (
        field_source == "user"
        and metadata.title.strip() == item.metadata.title.strip()
    ):
        # Clicking “store” does not mean the regex/PDF title was hand-curated.
        # Keep it replaceable so AI can correct it while preserving a genuinely
        # edited title as a user-owned field.
        record["curation"]["field_sources"]["bibliography.title"] = "auto:regex"
    return record
