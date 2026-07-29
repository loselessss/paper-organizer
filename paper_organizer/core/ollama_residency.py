"""Resolve Ollama model residency settings without starting the runtime."""

from __future__ import annotations

import re

from paper_organizer.core.model_recommendation import load_model_catalog


OLLAMA_RESIDENCY_CHOICES = (
    ("auto", "자동 (PC 사양에 맞춤)"),
    ("unload", "사용 후 즉시 해제"),
    ("5m", "5분 유지"),
    ("30m", "30분 유지"),
    ("always", "계속 상주"),
)
VALID_OLLAMA_RESIDENCY_MODES = {
    value for value, _label in OLLAMA_RESIDENCY_CHOICES
}


def resolve_ollama_keep_alive(
    mode: str,
    resident_model: str,
    request_model: str,
    memory_total_gb: float | None,
) -> int | str:
    """Return Ollama's keep_alive value for one request."""

    if not _same_model(resident_model or request_model, request_model):
        return 0
    if mode == "unload":
        return 0
    if mode == "5m":
        return "5m"
    if mode == "30m":
        return "30m"
    if mode == "always":
        return -1

    parameters = _model_parameters_b(request_model)
    memory = memory_total_gb or 0
    if memory >= 32 and parameters is not None and parameters <= 4:
        return "30m"
    if memory >= 24 and parameters is not None and parameters <= 2:
        return "15m"
    return "5m"


def residency_description(
    mode: str,
    resident_model: str,
    memory_total_gb: float | None,
) -> str:
    """Return concise Korean guidance for the settings dialog."""

    model = resident_model.strip() or "활성 Ollama 모델"
    if mode == "unload":
        policy = "각 요청이 끝나면 모델을 바로 해제해 메모리를 가장 적게 씁니다."
    elif mode == "5m":
        policy = "마지막 요청 뒤 5분 동안 유지해 짧은 연속 작업을 빠르게 처리합니다."
    elif mode == "30m":
        policy = "마지막 요청 뒤 30분 동안 유지합니다. 메모리 여유가 있는 PC에 적합합니다."
    elif mode == "always":
        policy = (
            "첫 요청 뒤 Ollama가 종료되거나 모델을 명시적으로 해제할 때까지 유지합니다. "
            "그동안 RAM·VRAM을 계속 사용합니다."
        )
    else:
        keep_alive = resolve_ollama_keep_alive(
            "auto", model, model, memory_total_gb
        )
        memory = (
            f"저장된 RAM {memory_total_gb:g}GB 기준"
            if memory_total_gb
            else "저장된 PC 사양이 없어 보수적으로"
        )
        policy = f"{memory} 마지막 요청 뒤 {keep_alive} 동안 유지합니다."
    return (
        f"{model}: {policy}\n"
        "앱 시작 시 미리 적재하지 않으며, 선택한 모델 하나만 상주 대상으로 둡니다."
    )


def _same_model(left: str, right: str) -> bool:
    return _model_key(left) == _model_key(right)


def _model_key(model: str) -> str:
    return model.strip().casefold().removesuffix(":latest")


def _model_parameters_b(model: str) -> float | None:
    key = _model_key(model)
    try:
        _version, specs = load_model_catalog()
        spec = next(
            (item for item in specs if _model_key(item.model_id) == key),
            None,
        )
    except (OSError, ValueError, KeyError, TypeError):
        spec = None
    if spec is not None:
        return spec.parameters_b
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?:\b|$)", key)
    return float(match.group(1)) if match else None
