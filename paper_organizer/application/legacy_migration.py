"""Safe migration of legacy PDF/JSON pairs into paperpack archives."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from paper_organizer.core.indexer import iter_sidecars, load_sidecar, rebuild_library_index
from paper_organizer.core.paperpack import (
    PAPERPACK_SUFFIX,
    PaperPackError,
    create_paperpack,
    load_paperpack_metadata,
    verify_paperpack,
)


class LegacyMigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LegacyMigrationCandidate:
    pdf_path: Path
    metadata_path: Path
    content_path: Path | None
    paperpack_path: Path
    title: str
    file_id: str


@dataclass(frozen=True, slots=True)
class LegacyMigrationProblem:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class LegacyMigrationPreview:
    candidates: tuple[LegacyMigrationCandidate, ...]
    problems: tuple[LegacyMigrationProblem, ...]
    already_migrated: int = 0


@dataclass(frozen=True, slots=True)
class LegacyMigrationItem:
    paperpack_path: Path
    legacy_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class LegacyMigrationResult:
    items: tuple[LegacyMigrationItem, ...]
    legacy_moved_to_trash: bool
    trash_operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyMigrationTrashEntry:
    operation_id: str
    manifest_path: Path
    created_at: str
    file_count: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
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


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


class LegacyMigrationService:
    def __init__(self, library_root: Path) -> None:
        self.library_root = library_root.expanduser().resolve()
        self.papers_root = (self.library_root / "papers").resolve()

    def preview(self) -> LegacyMigrationPreview:
        candidates: list[LegacyMigrationCandidate] = []
        problems: list[LegacyMigrationProblem] = []
        already_migrated = 0
        for metadata_path in iter_sidecars(self.library_root):
            try:
                candidate = self._candidate(metadata_path)
                if candidate.paperpack_path.is_file():
                    packed = load_paperpack_metadata(candidate.paperpack_path)
                    packed_id = str(
                        packed.get("identity", {}).get("file_id")
                        or packed.get("id")
                        or ""
                    )
                    if packed_id == candidate.file_id:
                        already_migrated += 1
                        continue
                    raise LegacyMigrationError(
                        "target paperpack exists but belongs to a different file"
                    )
                candidates.append(candidate)
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
                PaperPackError,
                LegacyMigrationError,
            ) as exc:
                problems.append(LegacyMigrationProblem(metadata_path, str(exc)))
        return LegacyMigrationPreview(
            tuple(sorted(candidates, key=lambda item: str(item.metadata_path).casefold())),
            tuple(sorted(problems, key=lambda item: str(item.path).casefold())),
            already_migrated,
        )

    def migrate(
        self,
        metadata_paths: Iterable[Path],
        *,
        move_legacy_to_trash: bool = False,
    ) -> LegacyMigrationResult:
        selected = [path.expanduser().resolve() for path in metadata_paths]
        if not selected:
            raise LegacyMigrationError("no legacy papers were selected")
        if len(selected) != len(set(selected)):
            raise LegacyMigrationError("the same legacy metadata was selected twice")
        try:
            candidates = [self._candidate(path) for path in selected]
        except Exception as exc:
            if isinstance(exc, LegacyMigrationError):
                raise
            raise LegacyMigrationError(f"could not validate legacy selection: {exc}") from None
        for candidate in candidates:
            if candidate.paperpack_path.exists():
                raise LegacyMigrationError(
                    f"target paperpack already exists: {candidate.paperpack_path}"
                )

        created: list[Path] = []
        items: list[LegacyMigrationItem] = []
        try:
            for candidate in candidates:
                record = copy.deepcopy(load_sidecar(candidate.metadata_path))
                content = (
                    _load_json_object(candidate.content_path)
                    if candidate.content_path is not None
                    else {}
                )
                file_data = record.setdefault("file", {})
                file_data["legacy_pdf_relative_path"] = str(
                    file_data.get("relative_path", "")
                )
                file_data["current_name"] = candidate.paperpack_path.name
                file_data["relative_path"] = candidate.paperpack_path.relative_to(
                    self.library_root
                ).as_posix()
                file_data["storage_format"] = "paperpack-zip-v1"
                record.setdefault("workflow", {})["migrated_to_paperpack_at"] = _now_iso()
                record.setdefault("provenance", {})["migration_source"] = (
                    "legacy-pdf-json"
                )
                create_paperpack(
                    candidate.paperpack_path,
                    candidate.pdf_path,
                    record,
                    content=content,
                    created_by="legacy-migration",
                )
                created.append(candidate.paperpack_path)
                legacy_paths = [candidate.pdf_path, candidate.metadata_path]
                if candidate.content_path is not None:
                    legacy_paths.append(candidate.content_path)
                items.append(
                    LegacyMigrationItem(
                        candidate.paperpack_path, tuple(legacy_paths)
                    )
                )
            rebuild_library_index(self.library_root)
        except Exception as exc:
            self._remove_created(created)
            try:
                rebuild_library_index(self.library_root)
            except Exception:
                pass
            raise LegacyMigrationError(f"legacy migration failed: {exc}") from None

        operation_id: str | None = None
        if move_legacy_to_trash:
            try:
                for path in created:
                    verify_paperpack(path)
                operation_id = self._move_to_trash(items)
            except Exception as exc:
                self._remove_created(created)
                try:
                    rebuild_library_index(self.library_root)
                except Exception:
                    pass
                raise LegacyMigrationError(
                    f"legacy cleanup failed and paperpacks were rolled back: {exc}"
                ) from None

        return LegacyMigrationResult(
            tuple(items), move_legacy_to_trash, operation_id
        )

    def list_trash(self) -> tuple[LegacyMigrationTrashEntry, ...]:
        trash_root = self.library_root / "trash"
        entries: list[LegacyMigrationTrashEntry] = []
        if not trash_root.is_dir():
            return ()
        for manifest in trash_root.glob("migration-*/manifest.json"):
            try:
                data = _load_json_object(manifest)
                if (
                    data.get("kind") != "legacy-paperpack-migration"
                    or data.get("restored_at")
                    or not isinstance(data.get("files"), list)
                ):
                    continue
                entries.append(
                    LegacyMigrationTrashEntry(
                        operation_id=str(data["operation_id"]),
                        manifest_path=manifest,
                        created_at=str(data.get("created_at", "")),
                        file_count=len(data["files"]),
                    )
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return tuple(sorted(entries, key=lambda item: item.created_at, reverse=True))

    def restore_trash(self, operation_id: str) -> tuple[Path, ...]:
        entry = next(
            (item for item in self.list_trash() if item.operation_id == operation_id),
            None,
        )
        if entry is None:
            raise LegacyMigrationError("migration trash operation was not found")
        operation_root = entry.manifest_path.parent.resolve()
        data = _load_json_object(entry.manifest_path)
        plans: list[tuple[Path, Path]] = []
        for item in data["files"]:
            if not isinstance(item, dict):
                raise LegacyMigrationError("migration trash manifest is invalid")
            source = (
                operation_root / str(item["trashed_relative_path"])
            ).resolve()
            destination = (
                self.library_root / str(item["original_relative_path"])
            ).resolve()
            if not _inside(operation_root, source) or not _inside(
                self.library_root, destination
            ):
                raise LegacyMigrationError("migration trash path is unsafe")
            if not source.is_file():
                raise LegacyMigrationError(f"trashed legacy file is missing: {source}")
            if destination.exists():
                raise LegacyMigrationError(
                    f"restore destination already exists: {destination}"
                )
            plans.append((source, destination))
        moved: list[tuple[Path, Path]] = []
        try:
            for source, destination in plans:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append((source, destination))
            data["restored_at"] = _now_iso()
            _atomic_json_write(entry.manifest_path, data)
            rebuild_library_index(self.library_root)
        except Exception as exc:
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(source))
            raise LegacyMigrationError(f"legacy restore failed: {exc}") from None
        return tuple(destination for _source, destination in plans)

    def _candidate(self, metadata_path: Path) -> LegacyMigrationCandidate:
        path = metadata_path.expanduser().resolve()
        if not _inside(self.papers_root, path) or not path.name.endswith(".paper.json"):
            raise LegacyMigrationError("metadata must be a library *.paper.json file")
        record = load_sidecar(path)
        relative_pdf = Path(str(record["file"]["relative_path"]))
        if relative_pdf.is_absolute():
            raise LegacyMigrationError("legacy PDF path must be relative")
        pdf_path = (self.library_root / relative_pdf).resolve()
        if not _inside(self.papers_root, pdf_path) or not pdf_path.is_file():
            raise LegacyMigrationError("linked legacy PDF is missing or outside papers")
        if pdf_path.suffix.casefold() != ".pdf":
            raise LegacyMigrationError("linked legacy document is not a PDF")
        content_path = Path(f"{pdf_path}.content.json")
        if content_path.is_file():
            _load_json_object(content_path)
        else:
            alternate = Path(str(path)[: -len(".paper.json")] + ".content.json")
            content_path = alternate if alternate.is_file() else None
            if content_path is not None:
                _load_json_object(content_path)
        paperpack_path = pdf_path.with_suffix(PAPERPACK_SUFFIX)
        identity = record.get("identity", {})
        file_id = str(identity.get("file_id") or record.get("id") or "")
        if not file_id:
            raise LegacyMigrationError("legacy record has no file_id")
        title = str(record.get("bibliography", {}).get("title") or pdf_path.stem)
        return LegacyMigrationCandidate(
            pdf_path, path, content_path, paperpack_path, title, file_id
        )

    def _move_to_trash(self, items: list[LegacyMigrationItem]) -> str:
        operation_id = (
            f"migration-{datetime.now().strftime('%Y%m%d-%H%M%S')}-"
            f"{uuid.uuid4().hex[:8]}"
        )
        operation_root = self.library_root / "trash" / operation_id
        moved: list[tuple[Path, Path]] = []
        try:
            for item in items:
                for source in item.legacy_paths:
                    relative = source.relative_to(self.library_root)
                    destination = operation_root / "files" / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(destination))
                    moved.append((source, destination))
            _atomic_json_write(
                operation_root / "manifest.json",
                {
                    "schema_version": 1,
                    "operation_id": operation_id,
                    "kind": "legacy-paperpack-migration",
                    "created_at": _now_iso(),
                    "restored_at": None,
                    "files": [
                        {
                            "original_relative_path": source.relative_to(
                                self.library_root
                            ).as_posix(),
                            "trashed_relative_path": destination.relative_to(
                                operation_root
                            ).as_posix(),
                        }
                        for source, destination in moved
                    ],
                    "paperpacks": [
                        item.paperpack_path.relative_to(self.library_root).as_posix()
                        for item in items
                    ],
                },
            )
        except Exception:
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(source))
            raise
        return operation_id

    @staticmethod
    def _remove_created(paths: Iterable[Path]) -> None:
        for path in paths:
            try:
                path.unlink()
            except OSError:
                pass
