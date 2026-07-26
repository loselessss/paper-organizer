"""Offline model catalog validation and conservative hardware recommendations."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_organizer.infra.hardware import HardwareProfile
from paper_organizer.infra.ollama_runtime import OllamaRuntimeStatus


VALID_MODEL_PROFILES = {"auto", "speed", "balanced", "quality", "manual"}


@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    label: str
    parameters_b: float
    download_gb: float
    runtime_memory_gb: float
    minimum_ram_gb: float
    recommended_ram_gb: float
    tier: str
    quality: int
    license: str
    recommended_context: int


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    spec: ModelSpec
    rating: str
    installed: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelRecommendation:
    catalog_version: str
    profile: str
    recommended: ModelCandidate | None
    candidates: tuple[ModelCandidate, ...]


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "models" / "catalog.json"


def load_model_catalog(path: Path | None = None) -> tuple[str, tuple[ModelSpec, ...]]:
    source = path or default_catalog_path()
    decoded = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict) or not isinstance(decoded.get("models"), list):
        raise ValueError("model catalog root is invalid")
    version = str(decoded.get("catalog_version") or "").strip()
    if not version:
        raise ValueError("model catalog version is required")
    models: list[ModelSpec] = []
    seen: set[str] = set()
    for raw in decoded["models"]:
        if not isinstance(raw, dict):
            raise ValueError("model catalog entry must be an object")
        spec = ModelSpec(
            model_id=str(raw["id"]).strip(),
            label=str(raw["label"]).strip(),
            parameters_b=float(raw["parameters_b"]),
            download_gb=float(raw["download_gb"]),
            runtime_memory_gb=float(raw["runtime_memory_gb"]),
            minimum_ram_gb=float(raw["minimum_ram_gb"]),
            recommended_ram_gb=float(raw["recommended_ram_gb"]),
            tier=str(raw["tier"]).strip(),
            quality=int(raw["quality"]),
            license=str(raw["license"]).strip(),
            recommended_context=int(raw["recommended_context"]),
        )
        if not spec.model_id or spec.model_id in seen:
            raise ValueError("model catalog contains an empty or duplicate id")
        numeric = (
            spec.parameters_b,
            spec.download_gb,
            spec.runtime_memory_gb,
            spec.minimum_ram_gb,
            spec.recommended_ram_gb,
            spec.recommended_context,
        )
        if any(not math.isfinite(value) or value <= 0 for value in numeric):
            raise ValueError(f"model catalog values must be positive: {spec.model_id}")
        seen.add(spec.model_id)
        models.append(spec)
    return version, tuple(sorted(models, key=lambda item: item.parameters_b))


def recommend_models(
    hardware: HardwareProfile,
    ollama: OllamaRuntimeStatus,
    *,
    profile: str = "auto",
    selected_model: str = "",
    catalog_path: Path | None = None,
) -> ModelRecommendation:
    if profile not in VALID_MODEL_PROFILES:
        raise ValueError(f"unsupported model profile: {profile}")
    version, specs = load_model_catalog(catalog_path)
    installed_names = _installed_aliases(ollama)
    candidates = tuple(
        _assess(spec, hardware, spec.model_id.casefold() in installed_names)
        for spec in specs
    )
    eligible = [candidate for candidate in candidates if candidate.rating != "비권장"]
    recommended: ModelCandidate | None = None
    selected_key = selected_model.strip().casefold()
    if profile == "manual":
        recommended = next(
            (
                candidate
                for candidate in candidates
                if candidate.spec.model_id.casefold() == selected_key
            ),
            None,
        )
    elif profile == "auto":
        installed = [candidate for candidate in eligible if candidate.installed]
        recommended = (
            _highest_stable(installed, hardware.memory_total_gb)
            if installed
            else _highest_stable(eligible, hardware.memory_total_gb)
        )
    elif profile == "speed":
        fast = [candidate for candidate in eligible if candidate.spec.parameters_b <= 4]
        recommended = _highest_stable(fast, hardware.memory_total_gb) or _smallest(eligible)
    elif profile == "balanced":
        balanced = [
            candidate for candidate in eligible if candidate.spec.parameters_b <= 8
        ]
        recommended = _highest_stable(
            balanced, hardware.memory_total_gb
        ) or _highest_stable(eligible, hardware.memory_total_gb)
    else:
        recommended = _highest_stable(eligible, hardware.memory_total_gb)
    return ModelRecommendation(version, profile, recommended, candidates)


def _assess(
    spec: ModelSpec, hardware: HardwareProfile, installed: bool
) -> ModelCandidate:
    reasons: list[str] = []
    warnings: list[str] = []
    required_disk = spec.download_gb * 1.5 + 2.0
    if hardware.memory_total_gb < spec.minimum_ram_gb:
        rating = "비권장"
        warnings.append(
            f"최소 RAM {spec.minimum_ram_gb:g}GB보다 시스템 RAM이 적습니다."
        )
    elif hardware.model_disk_free_gb < required_disk and not installed:
        rating = "비권장"
        warnings.append(f"안전한 다운로드에 약 {required_disk:.1f}GB 여유가 필요합니다.")
    elif (
        hardware.memory_total_gb >= spec.recommended_ram_gb
        and hardware.memory_available_gb >= min(
            spec.runtime_memory_gb + 2.0, hardware.memory_total_gb * 0.75
        )
    ):
        rating = "권장"
        reasons.append("Windows와 sPDF용 메모리 여유를 남길 수 있습니다.")
    else:
        rating = "사용 가능"
        warnings.append("현재 가용 RAM에 따라 CPU 처리 속도가 느리거나 스왑이 생길 수 있습니다.")
    if hardware.memory_available_gb < spec.runtime_memory_gb + 1.0:
        warnings.append(
            f"현재 가용 RAM {hardware.memory_available_gb:g}GB에서는 실행을 보류하고 "
            "다른 작업이 끝난 뒤 다시 확인해야 합니다."
        )
    best_vram = max(
        (gpu.vram_total_gb or 0 for gpu in hardware.gpus),
        default=0,
    )
    if best_vram >= spec.runtime_memory_gb:
        reasons.append(f"감지된 GPU VRAM {best_vram:g}GB 안에 들어갈 가능성이 높습니다.")
    elif hardware.gpus:
        reasons.append("GPU 일부 오프로딩 또는 CPU 실행 후보입니다.")
    else:
        reasons.append("GPU가 없어 CPU 실행 기준으로 평가했습니다.")
    reasons.append("이미 설치된 모델입니다." if installed else f"다운로드 약 {spec.download_gb:g}GB")
    return ModelCandidate(spec, rating, installed, tuple(reasons), tuple(warnings))


def _highest_stable(
    candidates: list[ModelCandidate], total_ram_gb: float
) -> ModelCandidate | None:
    if not candidates:
        return None
    recommended = [candidate for candidate in candidates if candidate.rating == "권장"]
    headroom = [
        candidate
        for candidate in candidates
        if candidate.spec.recommended_ram_gb <= total_ram_gb
    ]
    pool = recommended or headroom or candidates
    return max(pool, key=lambda item: (item.spec.quality, item.spec.parameters_b))


def _smallest(candidates: list[ModelCandidate]) -> ModelCandidate | None:
    return min(candidates, key=lambda item: item.spec.parameters_b) if candidates else None


def _installed_aliases(ollama: OllamaRuntimeStatus) -> set[str]:
    names: set[str] = set()
    for model in ollama.models:
        value = model.name.casefold()
        names.add(value)
        if value.endswith(":latest"):
            names.add(value.removesuffix(":latest"))
    return names
