"""Define local-model quality requirements for AI translation."""

from __future__ import annotations

import re

from paper_organizer.infra.settings import AppSettings


MINIMUM_TRANSLATION_PARAMETERS_B = 4.0
RECOMMENDED_TRANSLATION_PARAMETERS_B = 8.0


def ollama_model_parameters_b(model: str) -> float:
    """Return a catalog or model-tag parameter count, or zero when unknown."""

    key = model.strip().casefold().removesuffix(":latest")
    try:
        from paper_organizer.core.model_recommendation import load_model_catalog

        for spec in load_model_catalog()[1]:
            if spec.model_id.casefold().removesuffix(":latest") == key:
                return spec.parameters_b
    except (OSError, ValueError, KeyError):
        pass
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?:\b|$)", key)
    return float(match.group(1)) if match else 0.0


def require_translation_model(settings: AppSettings) -> None:
    """Reject local translation models below the supported quality floor."""

    if settings.summary_provider != "ollama":
        return
    model = settings.selected_model.strip()
    parameters = ollama_model_parameters_b(model)
    if parameters >= MINIMUM_TRANSLATION_PARAMETERS_B:
        return
    detail = f"현재 모델은 {parameters:g}B입니다." if parameters else "현재 모델 크기를 확인할 수 없습니다."
    raise ValueError(
        "AI 번역은 최소 4B 모델이 필요하며 8B 이상을 권장합니다. "
        f"{detail} 수동 요약 모델을 변경하세요."
    )
