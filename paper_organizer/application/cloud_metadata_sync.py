"""Two-copy metadata sync with portable JSON and explicit conflict resolution."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_organizer.core.indexer import (
    iter_record_paths,
    load_record,
    rebuild_library_index,
)
from paper_organizer.core.paperpack import (
    PAPERPACK_SUFFIX,
    PaperPackError,
    update_paperpack,
)


PORTABLE_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
EDITABLE_SECTIONS = (
    "bibliography",
    "classification",
    "description",
    "experimental_details",
)


class CloudMetadataSyncError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MetadataConflict:
    record_id: str
    title: str
    kind: str
    message: str
    local_record: dict[str, Any] | None = field(repr=False)
    cloud_record: dict[str, Any] | None = field(repr=False)
    sidecar_path: Path | None = None

    @property
    def can_use_cloud(self) -> bool:
        return self.local_record is not None and self.cloud_record is not None


@dataclass(frozen=True, slots=True)
class CloudSyncOutcome:
    portable_path: Path
    exported_records: int
    imported_records: int
    conflicts: tuple[MetadataConflict, ...]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
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


def _portable_record(record: dict[str, Any]) -> dict[str, Any]:
    identity = record.get("identity", {})
    file_data = record.get("file", {})
    record_id = str(identity.get("file_id") or record.get("id") or "")
    if not record_id:
        raise ValueError("identity.file_id is required for cloud sync")
    portable: dict[str, Any] = {
        "record_id": record_id,
        "work_id": str(identity.get("work_id", "")),
        "source_variant": str(identity.get("source_variant", "unknown")),
        "file_hint": {
            "current_name": str(file_data.get("current_name", "")),
            "relative_path": str(file_data.get("relative_path", "")),
        },
        "revision": int(record.get("curation", {}).get("revision", 0)),
    }
    for section in EDITABLE_SECTIONS:
        value = record.get(section, {})
        portable[section] = copy.deepcopy(value if isinstance(value, dict) else {})
    return portable


def _title(record: dict[str, Any] | None, record_id: str) -> str:
    if record:
        value = record.get("bibliography", {}).get("title")
        if value:
            return str(value)
        name = record.get("file_hint", {}).get("current_name")
        if name:
            return str(name)
    return record_id


class CloudMetadataSynchronizer:
    """Synchronize local authoritative sidecars with one portable cloud file."""

    def __init__(self, library_root: Path, sync_root: Path) -> None:
        self.library_root = library_root.expanduser().resolve()
        self.sync_root = sync_root.expanduser().resolve()
        self.portable_path = self.sync_root / "portable-library.json"
        self.report_path = self.sync_root / "sync-report.json"
        self.state_path = self.library_root / "state" / "cloud-sync-base.json"
        self.local_report_path = self.library_root / "state" / "cloud-sync-conflicts.json"

    def synchronize(self) -> CloudSyncOutcome:
        local, sidecars = self._local_records()
        cloud = self._load_cloud_records()
        bases = self._load_bases()
        merged = copy.deepcopy(cloud)
        next_bases = copy.deepcopy(bases)
        conflicts: list[MetadataConflict] = []
        imports: list[tuple[str, dict[str, Any], Path]] = []
        exported = 0

        for record_id in sorted(set(local) | set(cloud) | set(bases)):
            local_record = local.get(record_id)
            cloud_record = cloud.get(record_id)
            base_record = bases.get(record_id)
            if base_record is None:
                if local_record is not None and cloud_record is None:
                    merged[record_id] = local_record
                    next_bases[record_id] = local_record
                    exported += 1
                elif local_record == cloud_record and local_record is not None:
                    next_bases[record_id] = local_record
                elif local_record is None and cloud_record is not None:
                    conflicts.append(
                        self._conflict(
                            record_id,
                            "cloud_only",
                            "클라우드에만 있는 항목이며 연결할 로컬 PDF가 없습니다.",
                            local_record,
                            cloud_record,
                            sidecars.get(record_id),
                        )
                    )
                elif local_record is not None and cloud_record is not None:
                    conflicts.append(
                        self._conflict(
                            record_id,
                            "first_sync_difference",
                            "첫 동기화에서 로컬 원본과 클라우드 편집본이 다릅니다.",
                            local_record,
                            cloud_record,
                            sidecars.get(record_id),
                        )
                    )
                continue

            if local_record == cloud_record:
                if local_record is None:
                    next_bases.pop(record_id, None)
                    merged.pop(record_id, None)
                else:
                    next_bases[record_id] = local_record
                continue
            local_changed = local_record != base_record
            cloud_changed = cloud_record != base_record
            if local_record is not None and cloud_record is not None:
                if local_changed and not cloud_changed:
                    merged[record_id] = local_record
                    next_bases[record_id] = local_record
                    exported += 1
                elif cloud_changed and not local_changed:
                    imports.append((record_id, cloud_record, sidecars[record_id]))
                    next_bases[record_id] = cloud_record
                else:
                    conflicts.append(
                        self._conflict(
                            record_id,
                            "both_changed",
                            "마지막 동기화 후 로컬과 클라우드가 모두 수정됐습니다.",
                            local_record,
                            cloud_record,
                            sidecars.get(record_id),
                        )
                    )
            elif local_record is not None:
                conflicts.append(
                    self._conflict(
                        record_id,
                        "cloud_deleted",
                        "클라우드 항목이 삭제됐습니다. 로컬 PDF와 원본 JSON은 자동 삭제하지 않습니다.",
                        local_record,
                        None,
                        sidecars.get(record_id),
                    )
                )
            elif cloud_record is not None:
                conflicts.append(
                    self._conflict(
                        record_id,
                        "local_missing",
                        "로컬 원본 JSON 또는 PDF가 없어 클라우드 항목을 자동 적용할 수 없습니다.",
                        None,
                        cloud_record,
                        None,
                    )
                )

        if imports:
            self._apply_cloud_batch(imports)
        if merged != cloud or not self.portable_path.is_file():
            self._write_cloud_records(merged)
        self._write_bases(next_bases)
        self._write_report(conflicts, exported, len(imports))
        return CloudSyncOutcome(
            self.portable_path,
            exported_records=exported,
            imported_records=len(imports),
            conflicts=tuple(conflicts),
        )

    def conflicts(self) -> tuple[MetadataConflict, ...]:
        return self.synchronize().conflicts

    def resolve(self, record_id: str, choice: str) -> CloudSyncOutcome:
        if choice not in {"local", "cloud"}:
            raise CloudMetadataSyncError("충돌 해결 선택은 local 또는 cloud여야 합니다.")
        outcome = self.synchronize()
        conflict = next(
            (item for item in outcome.conflicts if item.record_id == record_id), None
        )
        if conflict is None:
            raise CloudMetadataSyncError("해결할 충돌을 찾을 수 없습니다.")
        cloud = self._load_cloud_records()
        bases = self._load_bases()
        if choice == "local":
            if conflict.local_record is None:
                cloud.pop(record_id, None)
                bases.pop(record_id, None)
            else:
                cloud[record_id] = conflict.local_record
                bases[record_id] = conflict.local_record
        else:
            if not conflict.can_use_cloud or conflict.sidecar_path is None:
                raise CloudMetadataSyncError(
                    "연결된 로컬 PDF와 클라우드 편집본이 모두 있을 때만 클라우드 값을 적용할 수 있습니다."
                )
            self._apply_cloud_batch(
                [(record_id, conflict.cloud_record, conflict.sidecar_path)]
            )
            cloud[record_id] = conflict.cloud_record
            bases[record_id] = conflict.cloud_record
        self._write_cloud_records(cloud)
        self._write_bases(bases)
        return self.synchronize()

    def _local_records(self) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
        records: dict[str, dict[str, Any]] = {}
        sidecars: dict[str, Path] = {}
        for sidecar in iter_record_paths(self.library_root):
            try:
                portable = _portable_record(load_record(sidecar))
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                json.JSONDecodeError,
                PaperPackError,
            ):
                continue
            record_id = portable["record_id"]
            if record_id in records:
                continue
            records[record_id] = portable
            sidecars[record_id] = sidecar
        return records, sidecars

    def _load_cloud_records(self) -> dict[str, dict[str, Any]]:
        if not self.portable_path.is_file():
            return {}
        try:
            data = json.loads(self.portable_path.read_text(encoding="utf-8"))
            if data.get("schema_version") != PORTABLE_SCHEMA_VERSION:
                raise ValueError("unsupported portable library schema")
            raw = data.get("papers", [])
            if not isinstance(raw, list):
                raise ValueError("portable papers must be a list")
            records = {}
            for item in raw:
                if not isinstance(item, dict) or not item.get("record_id"):
                    raise ValueError("portable paper record_id is required")
                records[str(item["record_id"])] = item
            return records
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CloudMetadataSyncError(f"클라우드 편집본을 읽을 수 없습니다: {exc}") from None

    def _load_bases(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.is_file():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if (
                data.get("schema_version") != STATE_SCHEMA_VERSION
                or data.get("sync_root") != str(self.sync_root)
            ):
                return {}
            bases = data.get("bases", {})
            return bases if isinstance(bases, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_cloud_records(self, records: dict[str, dict[str, Any]]) -> None:
        _atomic_json_write(
            self.portable_path,
            {
                "schema_version": PORTABLE_SCHEMA_VERSION,
                "format": "paper-organizer-portable-library",
                "updated_at": _now_iso(),
                "papers": [records[key] for key in sorted(records)],
            },
        )

    def _write_bases(self, bases: dict[str, dict[str, Any]]) -> None:
        _atomic_json_write(
            self.state_path,
            {
                "schema_version": STATE_SCHEMA_VERSION,
                "sync_root": str(self.sync_root),
                "updated_at": _now_iso(),
                "bases": bases,
            },
        )

    def _write_report(
        self, conflicts: list[MetadataConflict], exported: int, imported: int
    ) -> None:
        report = {
            "schema_version": 1,
            "updated_at": _now_iso(),
            "portable_file": self.portable_path.name,
            "exported_records": exported,
            "imported_records": imported,
            "conflict_count": len(conflicts),
            "conflicts": [
                {
                    "record_id": item.record_id,
                    "title": item.title,
                    "kind": item.kind,
                    "message": item.message,
                    "can_use_cloud": item.can_use_cloud,
                }
                for item in conflicts
            ],
        }
        _atomic_json_write(self.local_report_path, report)
        _atomic_json_write(self.report_path, report)

    def _apply_cloud_batch(
        self, updates: list[tuple[str, dict[str, Any], Path]]
    ) -> None:
        originals: dict[Path, dict[str, Any]] = {}
        try:
            for _record_id, portable, sidecar in updates:
                record = load_record(sidecar)
                originals[sidecar] = copy.deepcopy(record)
                self._backup_revision(record)
                for section in EDITABLE_SECTIONS:
                    value = portable.get(section, {})
                    if not isinstance(value, dict):
                        raise CloudMetadataSyncError(
                            f"클라우드 {section} 값은 JSON 객체여야 합니다."
                        )
                    record[section] = copy.deepcopy(value)
                curation = record.setdefault("curation", {})
                curation["revision"] = int(curation.get("revision", 0)) + 1
                curation["last_edited_at"] = _now_iso()
                curation["last_edited_by"] = "cloud_sync"
                record.setdefault("workflow", {})["updated_at"] = _now_iso()
                if sidecar.suffix.casefold() == PAPERPACK_SUFFIX:
                    update_paperpack(sidecar, record, changed_by="cloud_sync")
                else:
                    _atomic_json_write(sidecar, record)
            rebuild_library_index(self.library_root)
        except Exception as exc:
            for sidecar, original in originals.items():
                if sidecar.suffix.casefold() == PAPERPACK_SUFFIX:
                    try:
                        update_paperpack(sidecar, original, changed_by="rollback")
                    except PaperPackError:
                        pass
                else:
                    _atomic_json_write(sidecar, original)
            try:
                rebuild_library_index(self.library_root)
            except Exception:
                pass
            if isinstance(exc, CloudMetadataSyncError):
                raise
            raise CloudMetadataSyncError(f"클라우드 편집본 적용 실패: {exc}") from None

    def _backup_revision(self, record: dict[str, Any]) -> None:
        file_hash = str(record.get("file", {}).get("sha256") or "unknown").replace(":", "-")
        revision = int(record.get("curation", {}).get("revision", 0))
        history = self.library_root / "history" / file_hash
        backup = history / f"revision-{revision:04d}.paper.json"
        if not backup.exists():
            _atomic_json_write(backup, record)

    @staticmethod
    def _conflict(
        record_id: str,
        kind: str,
        message: str,
        local_record: dict[str, Any] | None,
        cloud_record: dict[str, Any] | None,
        sidecar_path: Path | None,
    ) -> MetadataConflict:
        return MetadataConflict(
            record_id=record_id,
            title=_title(local_record or cloud_record, record_id),
            kind=kind,
            message=message,
            local_record=copy.deepcopy(local_record),
            cloud_record=copy.deepcopy(cloud_record),
            sidecar_path=sidecar_path,
        )
