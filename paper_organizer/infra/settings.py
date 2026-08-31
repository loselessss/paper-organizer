"""Application settings with validation and atomic JSON persistence."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields, replace
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
    watch_subdirectories: bool = False
    library_root: str = ""
    remove_source_after_import: bool = False
    auto_enabled: bool = False
    auto_organize_academic: bool = True
    research_categories: list[str] = field(default_factory=list)
    research_subcategories: dict[str, list[str]] = field(default_factory=dict)
    focus_categories: list[str] = field(default_factory=list)
    resource_profile: str = "eco"
    background_analysis_enabled: bool = True
    bibliography_only: bool = False
    model_profile: str = "auto"
    selected_model: str = ""
    background_model: str = ""
    manual_model: str = ""
    background_model_resident: bool = False
    ollama_residency_mode: str = "auto"
    ollama_resident_model: str = ""
    ollama_force_igpu: bool = True
    ollama_model_benchmarks: dict[str, dict[str, Any]] = field(default_factory=dict)
    recommended_model: str = ""
    model_catalog_version: str = ""
    last_hardware_scan_at: str = ""
    hardware_profile: dict[str, Any] = field(default_factory=dict)
    managed_ollama_models: list[str] = field(default_factory=list)
    ollama_retirement_notice_acknowledged: bool = False
    summary_provider: str = "local"
    summary_language: str = "ko"
    summary_timeout_seconds: int = 900
    automatic_analysis_interval_seconds: int = 30
    manual_analysis_interval_seconds: int = 0
    openai_model: str = "gpt-5.6"
    anthropic_model: str = "claude-sonnet-4-6"
    cloud_processing_consent: bool = False
    cloud_request_profile: str = "conservative"
    cloud_max_parallel_requests: int = 1
    cloud_monthly_budget_usd: float = 0.0
    last_update_check_at: str = ""
    skipped_update_version: str = ""
    library_column_order: list[str] = field(default_factory=list)
    library_hidden_columns: list[str] = field(default_factory=list)
    minimum_age_seconds: int = 30
    scan_interval_seconds: int = 300

    def validate(self) -> None:
        if not isinstance(self.bibliography_only, bool):
            raise ValueError("서지 전용 설정은 참 또는 거짓이어야 합니다.")
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
        if not isinstance(self.watch_subdirectories, bool):
            raise ValueError("watch_subdirectories must be a boolean")
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
        if (
            not isinstance(self.research_subcategories, dict)
            or any(
                not isinstance(category, str)
                or not category.strip()
                or len(category.strip()) > 80
                or "," in category
                or not isinstance(subcategories, list)
                or any(
                    not isinstance(name, str)
                    or not name.strip()
                    or len(name.strip()) > 80
                    or "," in name
                    for name in subcategories
                )
                or len({name.strip().casefold() for name in subcategories})
                != len(subcategories)
                for category, subcategories in self.research_subcategories.items()
            )
        ):
            raise ValueError(
                "research_subcategories must map category names to unique names without commas"
            )
        if self.research_categories and not {
            name.strip().casefold() for name in self.research_subcategories
        }.issubset(
            {name.strip().casefold() for name in self.research_categories}
        ):
            raise ValueError(
                "research_subcategories keys must be selected research categories"
            )
        if self.model_profile not in {"auto", "speed", "balanced", "quality", "manual"}:
            raise ValueError("Unsupported model_profile")
        if not isinstance(self.background_model, str):
            raise ValueError("background_model must be a string")
        if not isinstance(self.manual_model, str):
            raise ValueError("manual_model must be a string")
        if not isinstance(self.background_model_resident, bool):
            raise ValueError("background_model_resident must be a boolean")
        if self.ollama_residency_mode not in {
            "auto",
            "unload",
            "5m",
            "30m",
            "always",
        }:
            raise ValueError("Unsupported ollama_residency_mode")
        if not isinstance(self.ollama_resident_model, str):
            raise ValueError("ollama_resident_model must be a string")
        if not isinstance(self.ollama_force_igpu, bool):
            raise ValueError("ollama_force_igpu must be a boolean")
        if (
            not isinstance(self.ollama_model_benchmarks, dict)
            or any(
                not isinstance(model, str)
                or not model.strip()
                or not isinstance(result, dict)
                for model, result in self.ollama_model_benchmarks.items()
            )
        ):
            raise ValueError("ollama_model_benchmarks must map models to JSON objects")
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
        if not isinstance(self.ollama_retirement_notice_acknowledged, bool):
            raise ValueError(
                "ollama_retirement_notice_acknowledged must be a boolean"
            )
        if self.summary_provider not in {"local", "ollama", "openai", "anthropic"}:
            raise ValueError(
                "summary_provider must be local, ollama, openai or anthropic"
            )
        if self.summary_language not in {"ko", "source"}:
            raise ValueError("summary_language must be ko or source")
        if (
            isinstance(self.summary_timeout_seconds, bool)
            or not isinstance(self.summary_timeout_seconds, int)
            or not 60 <= self.summary_timeout_seconds <= 3600
        ):
            raise ValueError("summary_timeout_seconds must be between 60 and 3600")
        for name, value in (
            (
                "automatic_analysis_interval_seconds",
                self.automatic_analysis_interval_seconds,
            ),
            (
                "manual_analysis_interval_seconds",
                self.manual_analysis_interval_seconds,
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 3600
            ):
                raise ValueError(f"{name} must be between 0 and 3600")
        if not isinstance(self.cloud_processing_consent, bool):
            raise ValueError("cloud_processing_consent must be a boolean")
        if not isinstance(self.last_update_check_at, str):
            raise ValueError("last_update_check_at must be a string")
        if not isinstance(self.skipped_update_version, str):
            raise ValueError("skipped_update_version must be a string")
        for name, value in (
            ("library_column_order", self.library_column_order),
            ("library_hidden_columns", self.library_hidden_columns),
        ):
            if (
                not isinstance(value, list)
                or any(not isinstance(column, str) or not column.strip() for column in value)
                or len({column.strip() for column in value}) != len(value)
            ):
                raise ValueError(f"{name} must contain unique non-empty column ids")
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
            effective_watch_folders = self.watch_folders or (
                [self.input_dir] if self.input_dir else []
            )
            for folder in effective_watch_folders:
                watch_path = Path(folder).expanduser().resolve()
                if os.path.normcase(str(watch_path)) == os.path.normcase(str(library_path)):
                    raise ValueError("watch folders and library_root must be different")
                if self.watch_subdirectories:
                    try:
                        library_path.relative_to(watch_path)
                    except ValueError:
                        pass
                    else:
                        raise ValueError(
                            "library_root cannot be inside a recursive watch folder"
                        )

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


def local_model_for_purpose(
    settings: AppSettings,
    purpose: str,
) -> str:
    """Return the configured local model for background or manual work."""

    if purpose == "background":
        return settings.background_model.strip() or settings.selected_model.strip()
    if purpose == "manual":
        return settings.manual_model.strip() or settings.selected_model.strip()
    raise ValueError("purpose must be background or manual")


def ollama_model_for_purpose(
    settings: AppSettings,
    purpose: str,
) -> str:
    """Compatibility alias for settings created before the embedded runtime."""

    return local_model_for_purpose(settings, purpose)


def settings_for_summary_purpose(
    settings: AppSettings,
    purpose: str,
) -> AppSettings:
    """Build request-local settings without mutating the persisted preferences."""

    if settings.summary_provider not in {"local", "ollama"}:
        return settings
    model = local_model_for_purpose(settings, purpose)
    if purpose == "background":
        residency_mode = (
            "always" if settings.background_model_resident else "unload"
        )
    elif purpose == "manual":
        residency_mode = "unload"
    else:
        raise ValueError("purpose must be background or manual")
    return replace(
        settings,
        selected_model=model,
        ollama_residency_mode=residency_mode,
        ollama_resident_model=model,
    )
