"""Coordinate read-only hardware and local AI model recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from paper_organizer.core.model_recommendation import (
    load_model_catalog,
    ModelRecommendation,
    recommend_models,
)
from paper_organizer.infra.embedded_llm_runtime import (
    default_model_dir,
    model_path_for_id,
)
from paper_organizer.infra.hardware import HardwareInspector, HardwareProfile
from paper_organizer.infra.ollama_runtime import (
    OllamaRuntimeInspector,
    OllamaRuntimeStatus,
)
from paper_organizer.infra.settings import (
    AppSettings,
    default_settings_path,
    load_settings,
    save_settings,
)


@dataclass(frozen=True, slots=True)
class LocalAiAssessment:
    hardware: HardwareProfile
    ollama: OllamaRuntimeStatus
    recommendation: ModelRecommendation
    local_model_count: int = 0
    local_model_dir: str = ""


class LocalAiAssessmentService:
    def __init__(
        self,
        settings_path: Path | None = None,
        hardware: HardwareInspector | None = None,
        ollama: OllamaRuntimeInspector | None = None,
    ) -> None:
        self._settings_path = settings_path or default_settings_path()
        self._hardware = hardware or HardwareInspector()
        self._ollama = ollama or OllamaRuntimeInspector()

    def scan(
        self,
        *,
        profile: str | None = None,
        selected_model: str | None = None,
        provider: str | None = None,
    ) -> LocalAiAssessment:
        settings = load_settings(self._settings_path)
        selected_profile = profile or settings.model_profile
        selected = settings.selected_model if selected_model is None else selected_model
        selected_provider = (provider or settings.summary_provider).strip().casefold()
        hardware = self._hardware.inspect()
        local_model_dir = default_model_dir()
        local_model_ids: tuple[str, ...] = ()
        if selected_provider == "ollama":
            ollama = self._ollama.inspect()
            installed_model_ids = None
        else:
            ollama = OllamaRuntimeStatus(False, "", ())
            local_model_ids = _installed_local_model_ids(local_model_dir)
            installed_model_ids = local_model_ids
        recommendation = recommend_models(
            hardware,
            ollama,
            profile=selected_profile,
            selected_model=selected,
            installed_model_ids=installed_model_ids,
        )
        snapshot = hardware.to_dict()
        snapshot["recommendation_profile"] = selected_profile
        settings.hardware_profile = snapshot
        settings.last_hardware_scan_at = hardware.detected_at
        settings.model_catalog_version = recommendation.catalog_version
        settings.recommended_model = (
            recommendation.recommended.spec.model_id
            if recommendation.recommended is not None
            else ""
        )
        save_settings(settings, self._settings_path)
        return LocalAiAssessment(
            hardware,
            ollama,
            recommendation,
            len(local_model_ids),
            str(local_model_dir),
        )

    def settings(self) -> AppSettings:
        return load_settings(self._settings_path)


def _installed_local_model_ids(model_dir: Path) -> tuple[str, ...]:
    try:
        _version, specs = load_model_catalog()
    except (OSError, ValueError, KeyError, TypeError):
        return ()
    found: list[str] = []
    for spec in specs:
        if model_path_for_id(spec.model_id, model_dir).is_file():
            found.append(spec.model_id)
    return tuple(found)
