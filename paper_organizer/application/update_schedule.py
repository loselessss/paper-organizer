"""Persist a low-noise once-per-day automatic update check schedule."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_organizer.infra.settings import (
    default_settings_path,
    load_settings,
    save_settings,
)


UPDATE_CHECK_INTERVAL = timedelta(days=1)


class UpdateCheckSchedule:
    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings_path = settings_path or default_settings_path()

    def is_due(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        raw = load_settings(self._settings_path).last_update_check_at.strip()
        if not raw:
            return True
        try:
            checked = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if checked > current:
            return True
        return current - checked >= UPDATE_CHECK_INTERVAL

    def mark_checked(self, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        settings = load_settings(self._settings_path)
        settings.last_update_check_at = current.astimezone(timezone.utc).isoformat()
        save_settings(settings, self._settings_path)

    def is_skipped(self, version: str) -> bool:
        return load_settings(self._settings_path).skipped_update_version == version.strip()

    def skip_version(self, version: str) -> None:
        normalized = version.strip()
        if not normalized:
            raise ValueError("건너뛸 업데이트 버전이 비어 있습니다.")
        settings = load_settings(self._settings_path)
        settings.skipped_update_version = normalized
        save_settings(settings, self._settings_path)
