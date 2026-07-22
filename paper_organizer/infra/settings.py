"""Application settings with validation and atomic JSON persistence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


def default_settings_path() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / "PaperOrganizer" / "settings.json"
    return Path.home() / ".paper-organizer" / "settings.json"


@dataclass(slots=True)
class AppSettings:
    schema_version: int = 1
    input_dir: str = ""
    library_root: str = ""
    auto_enabled: bool = False
    resource_profile: str = "eco"
    model_profile: str = "auto"
    selected_model: str = ""
    minimum_age_seconds: int = 30

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"Unsupported settings schema: {self.schema_version}")
        if self.resource_profile not in {"eco", "balanced", "performance"}:
            raise ValueError("resource_profile must be eco, balanced or performance")
        if self.model_profile not in {"auto", "speed", "balanced", "quality", "manual"}:
            raise ValueError("Unsupported model_profile")
        if self.minimum_age_seconds < 0:
            raise ValueError("minimum_age_seconds cannot be negative")
        if self.input_dir and self.library_root:
            input_path = Path(self.input_dir).expanduser().resolve()
            library_path = Path(self.library_root).expanduser().resolve()
            if os.path.normcase(str(input_path)) == os.path.normcase(str(library_path)):
                raise ValueError("input_dir and library_root must be different")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_settings(path: Path | None = None) -> AppSettings:
    target = path or default_settings_path()
    if not target.is_file():
        return AppSettings()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Settings root must be an object")
        allowed = {item.name for item in fields(AppSettings)}
        settings = AppSettings(**{key: value for key, value in raw.items() if key in allowed})
        settings.validate()
        return settings
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    settings.validate()
    target = path or default_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=".settings-", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(settings.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target
