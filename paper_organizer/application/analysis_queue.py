"""Small persistent queue for papers awaiting review or AI analysis."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


QUEUE_SCHEMA_VERSION = 1
VALID_STATUSES = {
    "pending_review",
    "organized_pending_analysis",
    "analyzing",
    "completed",
    "failed",
}


class AnalysisQueueError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AnalysisQueueItem:
    queue_id: str
    path: str
    file_sha256: str
    title: str
    status: str
    priority: int
    added_at: str
    updated_at: str
    last_error: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalysisQueueStore:
    def __init__(self, library_root: Path) -> None:
        self.library_root = library_root.expanduser().resolve()
        self.path = self.library_root / "state" / "analysis-queue.json"

    def load(self) -> list[AnalysisQueueItem]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != QUEUE_SCHEMA_VERSION:
                raise ValueError("unsupported analysis queue schema")
            raw_items = data.get("items", [])
            if not isinstance(raw_items, list):
                raise ValueError("analysis queue items must be a list")
            items: list[AnalysisQueueItem] = []
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise ValueError("analysis queue item must be an object")
                item = AnalysisQueueItem(**raw)
                if item.status not in VALID_STATUSES or item.priority not in (0, 1):
                    raise ValueError("invalid analysis queue item")
                items.append(item)
            return self._sorted(items)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AnalysisQueueError(f"분석 큐를 읽을 수 없습니다: {exc}") from None

    def enqueue(
        self,
        *,
        path: Path,
        file_sha256: str,
        title: str,
        status: str = "pending_review",
    ) -> AnalysisQueueItem:
        self._validate_status(status)
        items = self.load()
        queue_id = f"sha256:{file_sha256}"
        now = _now_iso()
        existing = next((item for item in items if item.queue_id == queue_id), None)
        item = AnalysisQueueItem(
            queue_id=queue_id,
            path=str(path.resolve()),
            file_sha256=file_sha256,
            title=title.strip() or path.stem,
            status=status if existing is None or existing.status != "completed" else "completed",
            priority=existing.priority if existing else 0,
            added_at=existing.added_at if existing else now,
            updated_at=now,
            last_error=existing.last_error if existing else "",
        )
        items = [value for value in items if value.queue_id != queue_id]
        items.append(item)
        self._save(items)
        return item

    def relocate(
        self, file_sha256: str, path: Path, *, status: str, title: str | None = None
    ) -> AnalysisQueueItem:
        self._validate_status(status)
        items = self.load()
        queue_id = f"sha256:{file_sha256}"
        existing = next((item for item in items if item.queue_id == queue_id), None)
        if existing is None:
            return self.enqueue(
                path=path,
                file_sha256=file_sha256,
                title=title or path.stem,
                status=status,
            )
        updated = AnalysisQueueItem(
            queue_id=existing.queue_id,
            path=str(path.resolve()),
            file_sha256=existing.file_sha256,
            title=(title or existing.title).strip(),
            status=status,
            priority=existing.priority,
            added_at=existing.added_at,
            updated_at=_now_iso(),
            last_error="",
        )
        self._replace(items, updated)
        return updated

    def set_priority(self, queue_id: str, high: bool) -> AnalysisQueueItem:
        items = self.load()
        existing = self._find(items, queue_id)
        updated = AnalysisQueueItem(
            **{
                **asdict(existing),
                "priority": 1 if high else 0,
                "updated_at": _now_iso(),
            }
        )
        self._replace(items, updated)
        return updated

    def remove(self, queue_id: str) -> None:
        items = self.load()
        remaining = [item for item in items if item.queue_id != queue_id]
        if len(remaining) == len(items):
            raise AnalysisQueueError("분석 큐 항목을 찾을 수 없습니다.")
        self._save(remaining)

    def _replace(
        self, items: list[AnalysisQueueItem], updated: AnalysisQueueItem
    ) -> None:
        self._save(
            [updated if item.queue_id == updated.queue_id else item for item in items]
        )

    @staticmethod
    def _find(items: list[AnalysisQueueItem], queue_id: str) -> AnalysisQueueItem:
        item = next((value for value in items if value.queue_id == queue_id), None)
        if item is None:
            raise AnalysisQueueError("분석 큐 항목을 찾을 수 없습니다.")
        return item

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in VALID_STATUSES:
            raise AnalysisQueueError(f"지원하지 않는 분석 큐 상태입니다: {status}")

    @staticmethod
    def _sorted(items: list[AnalysisQueueItem]) -> list[AnalysisQueueItem]:
        return sorted(items, key=lambda item: (-item.priority, item.added_at, item.title.casefold()))

    def _save(self, items: list[AnalysisQueueItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            prefix=".analysis-queue-", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    {
                        "schema_version": QUEUE_SCHEMA_VERSION,
                        "updated_at": _now_iso(),
                        "items": [asdict(item) for item in self._sorted(items)],
                    },
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
