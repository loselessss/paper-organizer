"""Application settings with validation and atomic JSON persistence."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
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
    first_run_completed: bool = False
    start_with_windows: bool = False
    close_behavior: str = "quit"
    input_dir: str = ""
    watch_folders: list[str] = field(default_factory=list)
    library_root: str = ""
    remove_source_after_import: bool = False
    auto_enabled: bool = False
    auto_organize_academic: bool = True
    research_categories: list[str] = field(default_factory=list)
    focus_categories: list[str] = field(default_factory=list)
    resource_profile: str = "eco"
    background_analysis_enabled: bool = True
    model_profile: str = "auto"
    selected_model: str = ""
    recommended_model: str = ""
    model_catalog_version: str = ""
    last_hardware_scan_at: str = ""
    hardware_profile: dict[str, Any] = field(default_factory=dict)
    managed_ollama_models: list[str] = field(default_factory=list)
    summary_provider: str = "ollama"
    summary_language: str = "ko"
    summary_timeout_seconds: int = 900
    openai_model: str = "gpt-5.6"
    anthropic_model: str = "claude-sonnet-4-6"
    cloud_processing_consent: bool = False
    cloud_request_profile: str = "conservative"
    cloud_max_parallel_requests: int = 1
    cloud_monthly_budget_usd: float = 0.0
    last_update_check_at: str = ""
    skipped_update_version: str = ""
    minimum_age_seconds: int = 30
    scan_interval_seconds: int = 300

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError(f"Unsupported settings schema: {self.schema_version}")
        if not isinstance(self.remove_source_after_import, bool):
            raise ValueError("remove_source_after_import must be a boolean")
        if not isinstance(self.first_run_completed, bool):
            raise ValueError("first_run_completed must be a boolean")
        if not isinstance(self.start_with_windows, bool):
            raise ValueError("start_with_windows must be a boolean")
        if self.close_behavior not in {"background", "quit"}:
            raise ValueError("close_behavior must be background or quit")
        if self.resource_profile not in {"eco", "balanced", "performance"}:
            raise ValueError("resource_profile must be eco, balanced or performance")
        if not isinstance(self.background_analysis_enabled, bool):
            raise ValueError("background_analysis_enabled must be a boolean")
        if not isinstance(self.auto_organize_academic, bool):
            raise ValueError("auto_organize_academic must be a boolean")
        if (
            not isinstance(self.watch_folders, list)
            or any(not isinstance(path, str) or not path.strip() for path in self.watch_folders)
            or len({os.path.normcase(str(Path(path).expanduser().resolve())) for path in self.watch_folders})
            != len(self.watch_folders)
        ):
            raise ValueError("watch_folders must contain unique non-empty paths")
        if not isinstance(self.focus_categories, list) or any(
            not isinstance(name, str) or not name.strip()
            for name in self.focus_categories
        ):
            raise ValueError("focus_categories must contain non-empty names")
        if (
            not isinstance(self.research_categories, list)
            or any(
                not isinstance(name, str)
                or not name.strip()
                or len(name.strip()) > 80
                or "," in name
                for name in self.research_categories
            )
            or len({name.strip().casefold() for name in self.research_categories})
            != len(self.research_categories)
        ):
            raise ValueError(
                "research_categories must contain unique names without commas"
            )
        if self.research_categories and not {
            name.strip().casefold() for name in self.focus_categories
        }.issubset(
            {name.strip().casefold() for name in self.research_categories}
        ):
            raise ValueError("focus_categories must be selected research categories")
        if self.model_profile not in {"auto", "speed", "balanced", "quality", "manual"}:
            raise ValueError("Unsupported model_profile")
        if not isinstance(self.hardware_profile, dict):
            raise ValueError("hardware_profile must be a JSON object")
        if (
            not isinstance(self.managed_ollama_models, list)
            or any(
                not isinstance(model, str) or not model.strip()
                for model in self.managed_ollama_models
            )
            or len({model.casefold() for model in self.managed_ollama_models})
            != len(self.managed_ollama_models)
        ):
            raise ValueError("managed_ollama_models must contain unique model names")
        if self.summary_provider not in {"ollama", "openai", "anthropic"}:
            raise ValueError("summary_provider must be ollama, openai or anthropic")
        if self.summary_language not in {"ko", "source"}:
            raise ValueError("summary_language must be ko or source")
        if (
            isinstance(self.summary_timeout_seconds, bool)
            or not isinstance(self.summary_timeout_seconds, int)
            or not 60 <= self.summary_timeout_seconds <= 3600
        ):
            raise ValueError("summary_timeout_seconds must be between 60 and 3600")
        if not isinstance(self.cloud_processing_consent, bool):
            raise ValueError("cloud_processing_consent must be a boolean")
        if not isinstance(self.last_update_check_at, str):
            raise ValueError("last_update_check_at must be a string")
        if not isinstance(self.skipped_update_version, str):
            raise ValueError("skipped_update_version must be a string")
        if self.cloud_request_profile not in {
            "conservative",
            "standard",
            "high_throughput",
        }:
            raise ValueError("Unsupported cloud_request_profile")
        if not 1 <= self.cloud_max_parallel_requests <= 16:
            raise ValueError("cloud_max_parallel_requests must be between 1 and 16")
        if (
            isinstance(self.cloud_monthly_budget_usd, bool)
            or not isinstance(self.cloud_monthly_budget_usd, (int, float))
            or not math.isfinite(self.cloud_monthly_budget_usd)
            or self.cloud_monthly_budget_usd < 0
        ):
            raise ValueError("cloud_monthly_budget_usd must be a finite non-negative number")
        if not self.openai_model.strip() or not self.anthropic_model.strip():
            raise ValueError("Cloud provider model names cannot be empty")
        if self.minimum_age_seconds < 0:
            raise ValueError("minimum_age_seconds cannot be negative")
        if not 5 <= self.scan_interval_seconds <= 3600:
            raise ValueError("scan_interval_seconds must be between 5 and 3600")
        if self.input_dir and self.library_root:
            input_path = Path(self.input_dir).expanduser().resolve()
            library_path = Path(self.library_root).expanduser().resolve()
            if os.path.normcase(str(input_path)) == os.path.normcase(str(library_path)):
                raise ValueError("input_dir and library_root must be different")
        if self.library_root:
            library_path = Path(self.library_root).expanduser().resolve()
            for folder in self.watch_folders:
                if os.path.normcase(str(Path(folder).expanduser().resolve())) == os.path.normcase(
                    str(library_path)
                ):
                    raise ValueError("watch folders and library_root must be different")

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
