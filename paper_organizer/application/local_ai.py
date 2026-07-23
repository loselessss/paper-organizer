"""Coordinate read-only hardware/Ollama inspection and model recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from paper_organizer.core.model_recommendation import (
    ModelRecommendation,
    recommend_models,
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
    ) -> LocalAiAssessment:
        settings = load_settings(self._settings_path)
        selected_profile = profile or settings.model_profile
        selected = settings.selected_model if selected_model is None else selected_model
        hardware = self._hardware.inspect()
        ollama = self._ollama.inspect()
        recommendation = recommend_models(
            hardware,
            ollama,
            profile=selected_profile,
            selected_model=selected,
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
        return LocalAiAssessment(hardware, ollama, recommendation)

    def settings(self) -> AppSettings:
        return load_settings(self._settings_path)
