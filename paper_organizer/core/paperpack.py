"""Portable ZIP-based storage for one paper and its editable index."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PAPERPACK_SUFFIX = ".paperpack"
PAPERPACK_SCHEMA_VERSION = 1
PAPERPACK_FORMAT = "paper-organizer-paperpack"
PAPERPACK_MIMETYPE = "application/vnd.paper-organizer.paperpack+zip"

MIMETYPE_ENTRY = "mimetype"
MANIFEST_ENTRY = "manifest.json"
PDF_ENTRY = "document/paper.pdf"
METADATA_ENTRY = "metadata/paper.json"
CONTENT_ENTRY = "content/content.json"
HISTORY_PREFIX = "history/"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_CONTENT_BYTES = 64 * 1024 * 1024
CONTENT_SCHEMA_VERSION = 1
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class PaperPackError(RuntimeError):
    """Raised when a paperpack is invalid or cannot be updated safely."""


@dataclass(frozen=True, slots=True)
class PaperPackInfo:
    path: Path
    original_name: str
    pdf_sha256: str
    pdf_size: int
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PaperPackExtraction:
    paperpack_path: Path
    pdf_path: Path
    pdf_sha256: str
    source_removed: bool


@dataclass(frozen=True, slots=True)
class PaperPackBatchResult:
    items: tuple[PaperPackExtraction, ...]
    sources_removed: bool


@dataclass(frozen=True, slots=True)
class PaperPackImportResult:
    paperpack: PaperPackInfo
    source_pdf: Path
    source_removed: bool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: dict[str, Any], label: str) -> bytes:
    if not isinstance(value, dict):
        raise PaperPackError(f"{label} must be a JSON object")
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _decode_object(value: bytes, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperPackError(f"invalid {label} JSON: {exc}") from None
    if not isinstance(decoded, dict):
        raise PaperPackError(f"{label} must be a JSON object")
    return decoded


def normalize_metadata_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return current metadata names, removing the former Korean-only summary key."""

    normalized = copy.deepcopy(metadata)
    legacy_name = "summary_ko"
    for container_name in ("description", "analysis"):
        container = normalized.get(container_name)
        if not isinstance(container, dict):
            continue
        if "summary" not in container and legacy_name in container:
            container["summary"] = container[legacy_name]
        container.pop(legacy_name, None)
    curation = normalized.get("curation")
    if isinstance(curation, dict):
        sources = curation.get("field_sources")
        if isinstance(sources, dict):
            old_path = f"description.{legacy_name}"
            if "description.summary" not in sources and old_path in sources:
                sources["description.summary"] = sources[old_path]
            sources.pop(old_path, None)
        locked = curation.get("locked_fields")
        if isinstance(locked, list):
            old_path = f"description.{legacy_name}"
            curation["locked_fields"] = [
                "description.summary" if value == old_path else value
                for value in locked
            ]
    return normalized


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _history_entry(revision: int) -> str:
    return f"{HISTORY_PREFIX}revision-{revision:04d}.json"


def _history_bytes(
    revision: int,
    metadata: dict[str, Any],
    content_sha256: str,
    changed_at: str,
    changed_by: str,
    change: dict[str, Any] | None = None,
) -> bytes:
    value: dict[str, Any] = {
        "revision": revision,
        "changed_at": changed_at,
        "changed_by": changed_by,
        "content_sha256": content_sha256,
        "metadata": metadata,
    }
    if change is not None:
        value["change"] = change
    return _json_bytes(value, "history")


def _manifest(
    *,
    original_name: str,
    pdf_sha256: str,
    pdf_size: int,
    metadata_bytes: bytes,
    content_bytes: bytes,
    revision: int,
    created_at: str,
    updated_at: str,
    created_by: str,
) -> dict[str, Any]:
    return {
        "format": PAPERPACK_FORMAT,
        "schema_version": PAPERPACK_SCHEMA_VERSION,
        "mimetype": PAPERPACK_MIMETYPE,
        "created_at": created_at,
        "updated_at": updated_at,
        "created_by": created_by,
        "revision": revision,
        "document": {
            "entry": PDF_ENTRY,
            "original_name": original_name,
            "media_type": "application/pdf",
            "sha256": pdf_sha256,
            "size_bytes": pdf_size,
        },
        "metadata": {
            "entry": METADATA_ENTRY,
            "sha256": _sha256_bytes(metadata_bytes),
            "size_bytes": len(metadata_bytes),
        },
        "content": {
            "entry": CONTENT_ENTRY,
            "sha256": _sha256_bytes(content_bytes),
            "size_bytes": len(content_bytes),
        },
        "history": {
            "entry_prefix": HISTORY_PREFIX,
            "revision_count": revision,
        },
    }


def _zip_info(name: str, *, stored: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.create_system = 0
    return info


def _write_bytes(
    archive: zipfile.ZipFile, name: str, value: bytes, *, stored: bool = False
) -> None:
    archive.writestr(_zip_info(name, stored=stored), value)


def _validate_names(archive: zipfile.ZipFile) -> set[str]:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise PaperPackError("paperpack contains duplicate entries")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise PaperPackError(f"unsafe paperpack entry: {name}")
    required = {
        MIMETYPE_ENTRY,
        MANIFEST_ENTRY,
        PDF_ENTRY,
        METADATA_ENTRY,
        CONTENT_ENTRY,
        _history_entry(1),
    }
    missing = required.difference(names)
    if missing:
        raise PaperPackError(f"paperpack is missing entries: {', '.join(sorted(missing))}")
    return set(names)


def _read_manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    names = _validate_names(archive)
    try:
        first = archive.infolist()[0]
        if first.filename != MIMETYPE_ENTRY or first.compress_type != zipfile.ZIP_STORED:
            raise PaperPackError("mimetype must be the first uncompressed entry")
        manifest_info = archive.getinfo(MANIFEST_ENTRY)
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise PaperPackError("paperpack manifest is too large")
        if archive.read(MIMETYPE_ENTRY).decode("ascii") != PAPERPACK_MIMETYPE:
            raise PaperPackError("paperpack mimetype is invalid")
        manifest = _decode_object(archive.read(MANIFEST_ENTRY), "manifest")
    except (KeyError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise PaperPackError(f"could not read paperpack manifest: {exc}") from None
    if manifest.get("format") != PAPERPACK_FORMAT:
        raise PaperPackError("file is not a Paper Organizer paperpack")
    if manifest.get("schema_version") != PAPERPACK_SCHEMA_VERSION:
        raise PaperPackError(
            f"unsupported paperpack schema version: {manifest.get('schema_version')}"
        )
    if manifest.get("mimetype") != PAPERPACK_MIMETYPE:
        raise PaperPackError("manifest mimetype is invalid")
    if manifest.get("document", {}).get("entry") != PDF_ENTRY:
        raise PaperPackError("manifest PDF entry is invalid")
    if manifest.get("metadata", {}).get("entry") != METADATA_ENTRY:
        raise PaperPackError("manifest metadata entry is invalid")
    if manifest.get("content", {}).get("entry") != CONTENT_ENTRY:
        raise PaperPackError("manifest content entry is invalid")
    if archive.getinfo(PDF_ENTRY).compress_type != zipfile.ZIP_STORED:
        raise PaperPackError("embedded PDF must be stored without compression")
    revision = int(manifest.get("revision", 0))
    if revision < 1:
        raise PaperPackError("paperpack revision must be positive")
    if int(manifest.get("history", {}).get("revision_count", 0)) != revision:
        raise PaperPackError("manifest revision count is inconsistent")
    expected_history = {_history_entry(number) for number in range(1, revision + 1)}
    actual_history = {
        name
        for name in names
        if name.startswith(HISTORY_PREFIX) and name.endswith(".json")
    }
    if actual_history != expected_history:
        raise PaperPackError("paperpack revision history is incomplete")
    return manifest


def _read_json_entry(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
    key: str,
    entry: str,
    maximum_bytes: int,
) -> bytes:
    info = archive.getinfo(entry)
    expected = manifest.get(key, {})
    declared_size = int(expected.get("size_bytes", -1))
    if info.file_size != declared_size:
        raise PaperPackError(f"{key} size mismatch")
    if info.file_size > maximum_bytes:
        raise PaperPackError(f"{key} JSON is too large")
    value = archive.read(entry)
    if _sha256_bytes(value) != str(expected.get("sha256", "")):
        raise PaperPackError(f"{key} checksum mismatch")
    return value


def _copy_entry(
    source: zipfile.ZipFile, destination: zipfile.ZipFile, name: str
) -> None:
    old_info = source.getinfo(name)
    new_info = _zip_info(name, stored=old_info.compress_type == zipfile.ZIP_STORED)
    with source.open(old_info, "r") as input_stream, destination.open(
        new_info, "w"
    ) as output_stream:
        shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)


def build_content_payload(
    page_texts: Iterable[str],
    *,
    extractor: str = "pymupdf",
    ocr_used: bool = False,
    ocr_complete: bool | None = None,
) -> dict[str, Any]:
    """Build the page-level full text stored in content/content.json.

    검색 DB(FTS)는 이 텍스트를 원천으로 재생성되므로 요약이나 AI 결과와 달리
    사람이 지우지 않는 한 항상 보존한다.
    """

    pages: list[dict[str, Any]] = []
    total_characters = 0
    for number, text in enumerate(page_texts, start=1):
        value = str(text or "")
        total_characters += len(value)
        pages.append({"page": number, "text": value})
    complete = bool(ocr_used) if ocr_complete is None else bool(ocr_complete)
    ocr_status = (
        "not-needed"
        if not ocr_used
        else ("complete" if complete else "partial")
    )
    return {
        "schema_version": CONTENT_SCHEMA_VERSION,
        "extractor": extractor,
        "ocr_used": bool(ocr_used),
        "ocr_status": ocr_status,
        "extracted_at": _now_iso(),
        "page_count": len(pages),
        "character_count": total_characters,
        "pages": pages,
    }


def content_pages(content: dict[str, Any] | None) -> list[tuple[int, str]]:
    """Return (page number, text) pairs from a stored content payload."""

    if not isinstance(content, dict):
        return []
    raw_pages = content.get("pages")
    if not isinstance(raw_pages, list):
        return []
    pages: list[tuple[int, str]] = []
    for index, entry in enumerate(raw_pages, start=1):
        if not isinstance(entry, dict):
            continue
        try:
            number = int(entry.get("page", index))
        except (TypeError, ValueError):
            number = index
        text = entry.get("text")
        if isinstance(text, str) and text.strip():
            pages.append((number, text))
    return pages


def create_paperpack(
    destination: Path,
    pdf_path: Path,
    metadata: dict[str, Any],
    *,
    content: dict[str, Any] | None = None,
    created_by: str = "paper-organizer",
) -> PaperPackInfo:
    """Create a standard ZIP paperpack atomically without changing the PDF."""

    target = destination.expanduser().resolve()
    source = pdf_path.expanduser().resolve()
    if target.suffix.casefold() != PAPERPACK_SUFFIX:
        raise PaperPackError(f"paperpack filename must end with {PAPERPACK_SUFFIX}")
    if target.exists():
        raise PaperPackError(f"paperpack already exists: {target}")
    if not source.is_file():
        raise PaperPackError(f"PDF not found: {source}")
    with source.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise PaperPackError("source is not a PDF")
    pdf_sha256, pdf_size = _sha256_file(source)
    metadata = normalize_metadata_fields(metadata)
    metadata_bytes = _json_bytes(metadata, "metadata")
    content_bytes = _json_bytes(content or {}, "content")
    now = _now_iso()
    manifest = _manifest(
        original_name=source.name,
        pdf_sha256=pdf_sha256,
        pdf_size=pdf_size,
        metadata_bytes=metadata_bytes,
        content_bytes=content_bytes,
        revision=1,
        created_at=now,
        updated_at=now,
        created_by=created_by,
    )
    history = _history_bytes(
        1, metadata, _sha256_bytes(content_bytes), now, created_by
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".tmp", dir=str(target.parent)
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", allowZip64=True) as archive:
            _write_bytes(
                archive, MIMETYPE_ENTRY, PAPERPACK_MIMETYPE.encode("ascii"), stored=True
            )
            _write_bytes(archive, MANIFEST_ENTRY, _json_bytes(manifest, "manifest"))
            archive.write(source, PDF_ENTRY, compress_type=zipfile.ZIP_STORED)
            _write_bytes(archive, METADATA_ENTRY, metadata_bytes)
            _write_bytes(archive, CONTENT_ENTRY, content_bytes)
            _write_bytes(archive, _history_entry(1), history)
        verify_paperpack(temp_path)
        os.replace(temp_path, target)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return inspect_paperpack(target)


def import_pdf_to_paperpack(
    destination: Path,
    pdf_path: Path,
    metadata: dict[str, Any],
    *,
    content: dict[str, Any] | None = None,
    created_by: str = "paper-organizer",
    remove_source: bool = False,
) -> PaperPackImportResult:
    """Import a PDF and optionally remove it after package verification.

    Source preservation is the default. When removal is requested, the source is
    hashed again after creation. A cleanup failure rolls back the newly created
    paperpack so the operation never reports a completed move while both copies
    unexpectedly remain.
    """

    source = pdf_path.expanduser().resolve()
    target = destination.expanduser().resolve()
    info = create_paperpack(
        target,
        source,
        metadata,
        content=content,
        created_by=created_by,
    )
    if not remove_source:
        return PaperPackImportResult(info, source, False)
    try:
        source_sha256, source_size = _sha256_file(source)
        if source_sha256 != info.pdf_sha256 or source_size != info.pdf_size:
            raise PaperPackError(
                "source PDF changed after paperpack creation; source was not removed"
            )
        source.unlink()
    except (OSError, PaperPackError) as exc:
        try:
            target.unlink()
        except OSError as rollback_exc:
            raise PaperPackError(
                "source PDF removal failed and the new paperpack could not be rolled "
                f"back: {exc}; rollback: {rollback_exc}"
            ) from None
        if isinstance(exc, PaperPackError):
            raise
        raise PaperPackError(
            f"source PDF removal failed; new paperpack was rolled back: {exc}"
        ) from None
    return PaperPackImportResult(info, source, True)


def inspect_paperpack(path: Path) -> PaperPackInfo:
    try:
        with zipfile.ZipFile(path.expanduser().resolve(), "r") as archive:
            manifest = _read_manifest(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PaperPackError(f"invalid paperpack ZIP: {exc}") from None
    document = manifest.get("document", {})
    return PaperPackInfo(
        path=path.expanduser().resolve(),
        original_name=str(document.get("original_name", "")),
        pdf_sha256=str(document.get("sha256", "")),
        pdf_size=int(document.get("size_bytes", 0)),
        revision=int(manifest.get("revision", 0)),
        created_at=str(manifest.get("created_at", "")),
        updated_at=str(manifest.get("updated_at", "")),
    )


def load_paperpack_metadata(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path.expanduser().resolve(), "r") as archive:
            manifest = _read_manifest(archive)
            return normalize_metadata_fields(
                _decode_object(
                    _read_json_entry(
                        archive,
                        manifest,
                        "metadata",
                        METADATA_ENTRY,
                        MAX_METADATA_BYTES,
                    ),
                    "metadata",
                )
            )
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise PaperPackError(f"could not read paperpack metadata: {exc}") from None


def load_paperpack_content(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path.expanduser().resolve(), "r") as archive:
            manifest = _read_manifest(archive)
            return _decode_object(
                _read_json_entry(
                    archive,
                    manifest,
                    "content",
                    CONTENT_ENTRY,
                    MAX_CONTENT_BYTES,
                ),
                "content",
            )
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise PaperPackError(f"could not read paperpack content: {exc}") from None


def update_paperpack(
    path: Path,
    metadata: dict[str, Any],
    *,
    content: dict[str, Any] | None = None,
    changed_by: str = "user",
) -> PaperPackInfo:
    """Atomically rewrite JSON entries while preserving the embedded PDF and history."""

    target = path.expanduser().resolve()
    metadata = normalize_metadata_fields(metadata)
    metadata_bytes = _json_bytes(metadata, "metadata")
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".tmp", dir=str(target.parent)
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(target, "r") as source:
            old_manifest = _read_manifest(source)
            content_bytes = (
                _json_bytes(content, "content")
                if content is not None
                else _read_json_entry(
                    source,
                    old_manifest,
                    "content",
                    CONTENT_ENTRY,
                    MAX_CONTENT_BYTES,
                )
            )
            revision = int(old_manifest["revision"]) + 1
            now = _now_iso()
            document = old_manifest["document"]
            manifest = _manifest(
                original_name=str(document["original_name"]),
                pdf_sha256=str(document["sha256"]),
                pdf_size=int(document["size_bytes"]),
                metadata_bytes=metadata_bytes,
                content_bytes=content_bytes,
                revision=revision,
                created_at=str(old_manifest["created_at"]),
                updated_at=now,
                created_by=str(old_manifest.get("created_by", "paper-organizer")),
            )
            history_names = sorted(
                name
                for name in source.namelist()
                if name.startswith(HISTORY_PREFIX) and name.endswith(".json")
            )
            reserved = {
                MIMETYPE_ENTRY,
                MANIFEST_ENTRY,
                PDF_ENTRY,
                METADATA_ENTRY,
                CONTENT_ENTRY,
                *history_names,
            }
            extension_names = [
                name for name in source.namelist() if name not in reserved
            ]
            with zipfile.ZipFile(temp_path, "w", allowZip64=True) as destination:
                _write_bytes(
                    destination,
                    MIMETYPE_ENTRY,
                    PAPERPACK_MIMETYPE.encode("ascii"),
                    stored=True,
                )
                _write_bytes(
                    destination, MANIFEST_ENTRY, _json_bytes(manifest, "manifest")
                )
                _copy_entry(source, destination, PDF_ENTRY)
                _write_bytes(destination, METADATA_ENTRY, metadata_bytes)
                _write_bytes(destination, CONTENT_ENTRY, content_bytes)
                for name in history_names:
                    _copy_entry(source, destination, name)
                _write_bytes(
                    destination,
                    _history_entry(revision),
                    _history_bytes(
                        revision,
                        metadata,
                        _sha256_bytes(content_bytes),
                        now,
                        changed_by,
                    ),
                )
                for name in extension_names:
                    _copy_entry(source, destination, name)
        verify_paperpack(temp_path)
        os.replace(temp_path, target)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return inspect_paperpack(target)


def replace_paperpack_pdf(
    path: Path,
    pdf_path: Path,
    metadata: dict[str, Any],
    *,
    content: dict[str, Any] | None = None,
    expected_pdf_sha256: str | None = None,
    expected_revision: int | None = None,
    changed_by: str = "user",
) -> PaperPackInfo:
    """Atomically replace the embedded PDF after optimistic-lock checks.

    The caller supplies the metadata that describes the new PDF. The current
    package is left untouched when the edit is not a PDF, the package changed
    since the working copy was created, or verification of the replacement
    archive fails.
    """

    target = path.expanduser().resolve()
    edited = pdf_path.expanduser().resolve()
    if not edited.is_file():
        raise PaperPackError(f"edited PDF not found: {edited}")
    with edited.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise PaperPackError("edited file is not a PDF")
    edited_sha256, edited_size = _sha256_file(edited)
    metadata = normalize_metadata_fields(metadata)
    metadata_bytes = _json_bytes(metadata, "metadata")
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".tmp", dir=str(target.parent)
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(target, "r") as source:
            old_manifest = _read_manifest(source)
            document = old_manifest["document"]
            old_sha256 = str(document["sha256"])
            old_revision = int(old_manifest["revision"])
            if expected_pdf_sha256 is not None and old_sha256 != expected_pdf_sha256:
                raise PaperPackError(
                    "paperpack PDF changed after the editable copy was created"
                )
            if expected_revision is not None and old_revision != expected_revision:
                raise PaperPackError(
                    "paperpack revision changed after the editable copy was created"
                )
            if edited_sha256 == old_sha256:
                raise PaperPackError("edited PDF is unchanged")
            content_bytes = (
                _json_bytes(content, "content")
                if content is not None
                else _read_json_entry(
                    source,
                    old_manifest,
                    "content",
                    CONTENT_ENTRY,
                    MAX_CONTENT_BYTES,
                )
            )
            revision = old_revision + 1
            now = _now_iso()
            manifest = _manifest(
                original_name=str(document["original_name"]),
                pdf_sha256=edited_sha256,
                pdf_size=edited_size,
                metadata_bytes=metadata_bytes,
                content_bytes=content_bytes,
                revision=revision,
                created_at=str(old_manifest["created_at"]),
                updated_at=now,
                created_by=str(old_manifest.get("created_by", "paper-organizer")),
            )
            history_names = sorted(
                name
                for name in source.namelist()
                if name.startswith(HISTORY_PREFIX) and name.endswith(".json")
            )
            reserved = {
                MIMETYPE_ENTRY,
                MANIFEST_ENTRY,
                PDF_ENTRY,
                METADATA_ENTRY,
                CONTENT_ENTRY,
                *history_names,
            }
            extension_names = [
                name for name in source.namelist() if name not in reserved
            ]
            with zipfile.ZipFile(temp_path, "w", allowZip64=True) as destination:
                _write_bytes(
                    destination,
                    MIMETYPE_ENTRY,
                    PAPERPACK_MIMETYPE.encode("ascii"),
                    stored=True,
                )
                _write_bytes(
                    destination, MANIFEST_ENTRY, _json_bytes(manifest, "manifest")
                )
                destination.write(edited, PDF_ENTRY, compress_type=zipfile.ZIP_STORED)
                _write_bytes(destination, METADATA_ENTRY, metadata_bytes)
                _write_bytes(destination, CONTENT_ENTRY, content_bytes)
                for name in history_names:
                    _copy_entry(source, destination, name)
                _write_bytes(
                    destination,
                    _history_entry(revision),
                    _history_bytes(
                        revision,
                        metadata,
                        _sha256_bytes(content_bytes),
                        now,
                        changed_by,
                        change={
                            "kind": "pdf_replaced",
                            "previous_pdf_sha256": old_sha256,
                            "pdf_sha256": edited_sha256,
                            "size_bytes": edited_size,
                        },
                    ),
                )
                for name in extension_names:
                    _copy_entry(source, destination, name)
        verify_paperpack(temp_path)
        os.replace(temp_path, target)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise
    return inspect_paperpack(target)


def extract_paperpack_pdf(path: Path, destination: Path) -> Path:
    """Extract and checksum the PDF using bounded memory and atomic replacement."""

    target = destination.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".tmp", dir=str(target.parent)
    )
    digest = hashlib.sha256()
    size = 0
    try:
        with zipfile.ZipFile(path.expanduser().resolve(), "r") as archive:
            manifest = _read_manifest(archive)
            with archive.open(PDF_ENTRY, "r") as input_stream, os.fdopen(
                handle, "wb"
            ) as output_stream:
                while chunk := input_stream.read(1024 * 1024):
                    output_stream.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                output_stream.flush()
                os.fsync(output_stream.fileno())
        expected = manifest["document"]
        if digest.hexdigest() != str(expected["sha256"]) or size != int(
            expected["size_bytes"]
        ):
            raise PaperPackError("embedded PDF checksum mismatch")
        os.replace(temp_name, target)
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


def extract_paperpack_pdfs(
    paths: Iterable[Path],
    destination_dir: Path,
    *,
    remove_sources: bool = False,
) -> PaperPackBatchResult:
    """Extract multiple PDFs; remove sources only after the whole batch verifies.

    Existing output files are never overwritten. If validation or extraction fails,
    outputs created by this call are rolled back and every source paperpack remains.
    """

    sources = [path.expanduser().resolve() for path in paths]
    if not sources:
        raise PaperPackError("no paperpacks were selected")
    if len(sources) != len(set(sources)):
        raise PaperPackError("the same paperpack was selected more than once")
    for source in sources:
        if source.suffix.casefold() != PAPERPACK_SUFFIX:
            raise PaperPackError(f"not a paperpack: {source}")

    output_root = destination_dir.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    reserved_names = {
        path.name.casefold() for path in output_root.iterdir() if path.is_file()
    }
    plans: list[tuple[Path, Path, PaperPackInfo, tuple[int, int]]] = []
    for source in sources:
        info = verify_paperpack(source)
        stat = source.stat()
        name = _safe_pdf_name(info.original_name, source.stem)
        output = _unique_pdf_output(output_root, name, reserved_names)
        reserved_names.add(output.name.casefold())
        plans.append((source, output, info, (stat.st_size, stat.st_mtime_ns)))

    created: list[Path] = []
    try:
        for source, output, info, _signature in plans:
            extract_paperpack_pdf(source, output)
            digest, size = _sha256_file(output)
            if digest != info.pdf_sha256 or size != info.pdf_size:
                raise PaperPackError(f"extracted PDF verification failed: {output}")
            created.append(output)
    except Exception:
        for output in created:
            try:
                output.unlink()
            except OSError:
                pass
        raise

    if remove_sources:
        changed_source: Path | None = None
        stat_error: OSError | None = None
        try:
            for source, _output, _info, signature in plans:
                current = source.stat()
                if (current.st_size, current.st_mtime_ns) != signature:
                    changed_source = source
                    break
        except OSError as exc:
            stat_error = exc
        if changed_source is not None or stat_error is not None:
            for output in created:
                try:
                    output.unlink()
                except OSError:
                    pass
            detail = str(changed_source) if changed_source else str(stat_error)
            raise PaperPackError(
                "paperpack changed during export; no sources were removed: " + detail
            )
        removed: list[Path] = []
        try:
            for source, _output, _info, _signature in plans:
                source.unlink()
                removed.append(source)
        except OSError as exc:
            raise PaperPackError(
                "PDF extraction succeeded, but source removal was only partially "
                f"completed ({len(removed)}/{len(plans)}): {exc}"
            ) from None

    return PaperPackBatchResult(
        items=tuple(
            PaperPackExtraction(
                paperpack_path=source,
                pdf_path=output,
                pdf_sha256=info.pdf_sha256,
                source_removed=remove_sources,
            )
            for source, output, info, _signature in plans
        ),
        sources_removed=remove_sources,
    )


def _safe_pdf_name(original_name: str, fallback_stem: str) -> str:
    name = original_name.replace("\\", "/").rsplit("/", 1)[-1]
    stem = Path(name).stem if name.casefold().endswith(".pdf") else name
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", stem)
    stem = " ".join(stem.split()).rstrip(" .") or fallback_stem
    stem = stem[:180].rstrip(" .") or "paper"
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem}.pdf"


def _unique_pdf_output(
    directory: Path, preferred_name: str, reserved_names: set[str]
) -> Path:
    preferred = Path(preferred_name)
    candidate = directory / preferred.name
    number = 2
    while candidate.name.casefold() in reserved_names or candidate.exists():
        candidate = directory / f"{preferred.stem} ({number}).pdf"
        number += 1
    return candidate


def verify_paperpack(path: Path, *, verify_pdf: bool = True) -> PaperPackInfo:
    try:
        with zipfile.ZipFile(path.expanduser().resolve(), "r") as archive:
            bad_entry = archive.testzip()
            if bad_entry:
                raise PaperPackError(f"ZIP CRC check failed: {bad_entry}")
            manifest = _read_manifest(archive)
            metadata = _read_json_entry(
                archive,
                manifest,
                "metadata",
                METADATA_ENTRY,
                MAX_METADATA_BYTES,
            )
            content = _read_json_entry(
                archive,
                manifest,
                "content",
                CONTENT_ENTRY,
                MAX_CONTENT_BYTES,
            )
            _decode_object(metadata, "metadata")
            _decode_object(content, "content")
            if verify_pdf:
                digest = hashlib.sha256()
                size = 0
                with archive.open(PDF_ENTRY, "r") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                expected_pdf = manifest["document"]
                if size != int(expected_pdf["size_bytes"]):
                    raise PaperPackError("embedded PDF size mismatch")
                if digest.hexdigest() != str(expected_pdf["sha256"]):
                    raise PaperPackError("embedded PDF checksum mismatch")
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        if isinstance(exc, PaperPackError):
            raise
        raise PaperPackError(f"invalid paperpack ZIP: {exc}") from None
    return inspect_paperpack(path)


def iter_paperpacks(library_root: Path) -> Iterable[Path]:
    papers = library_root / "papers"
    if not papers.is_dir():
        return ()
    return papers.rglob(f"*{PAPERPACK_SUFFIX}")
