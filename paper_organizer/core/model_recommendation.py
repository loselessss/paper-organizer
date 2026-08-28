"""Offline model catalog validation and conservative hardware recommendations."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from paper_organizer.infra.hardware import HardwareProfile
from paper_organizer.infra.ollama_runtime import OllamaRuntimeStatus


VALID_MODEL_PROFILES = {"auto", "speed", "balanced", "quality", "manual"}
BACKGROUND_RECOMMENDED_MODEL = "qwen3:1.7b"
LOCAL_AI_MINIMUM_TOTAL_RAM_GB = 12.0
LOCAL_AI_SYSTEM_MEMORY_RESERVE_GB = 0.5


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
    recommendation_rank: int | None
    download_priority: int | None
    benchmark_score: float | None
    benchmark_paper_count: int
    benchmark_success_count: int
    benchmark_average_seconds: float | None
    benchmark_json_retries: int | None
    benchmark_bibliography_retries: int | None
    benchmark_research_score: float | None
    benchmark_review_score: float | None
    benchmark_bibliography_score: float | None
    benchmark_strengths: tuple[str, ...]
    benchmark_hardware: str
    download_url: str = ""
    sha256: str = ""


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


@dataclass(frozen=True, slots=True)
class ModelUsageGuidance:
    role: str
    hallucination_risk: str
    summary_strategy: str
    advanced_analysis: bool
    caution: str

    def display_text(self) -> str:
        advanced = "기여·한계 지원" if self.advanced_analysis else "기여·한계 미지원"
        return (
            f"{self.role} · 환각 위험 {self.hallucination_risk}\n"
            f"{self.summary_strategy} · {advanced}\n"
            f"{self.caution}"
        )


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
    benchmark_hardware = str(decoded.get("benchmark_hardware") or "").strip()
    models: list[ModelSpec] = []
    seen: set[str] = set()
    for raw in decoded["models"]:
        if not isinstance(raw, dict):
            raise ValueError("model catalog entry must be an object")
        benchmark = raw.get("benchmark") or {}
        if not isinstance(benchmark, dict):
            raise ValueError("model benchmark entry must be an object")
        recommendation_rank = raw.get("recommendation_rank")
        download_priority = raw.get("download_priority")
        benchmark_score = benchmark.get("score")
        benchmark_average_seconds = benchmark.get("average_seconds")
        benchmark_json_retries = benchmark.get("json_retries")
        benchmark_bibliography_retries = benchmark.get("bibliography_retries")
        strengths = benchmark.get("strengths") or []
        if not isinstance(strengths, list):
            raise ValueError("model benchmark strengths must be a list")
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
            recommendation_rank=(
                int(recommendation_rank)
                if recommendation_rank is not None
                else None
            ),
            download_priority=(
                int(download_priority) if download_priority is not None else None
            ),
            benchmark_score=(
                float(benchmark_score) if benchmark_score is not None else None
            ),
            benchmark_paper_count=int(benchmark.get("paper_count") or 0),
            benchmark_success_count=int(benchmark.get("success_count") or 0),
            benchmark_average_seconds=(
                float(benchmark_average_seconds)
                if benchmark_average_seconds is not None
                else None
            ),
            benchmark_json_retries=(
                int(benchmark_json_retries)
                if benchmark_json_retries is not None
                else None
            ),
            benchmark_bibliography_retries=(
                int(benchmark_bibliography_retries)
                if benchmark_bibliography_retries is not None
                else None
            ),
            benchmark_research_score=_optional_score(
                benchmark.get("research_score_100")
            ),
            benchmark_review_score=_optional_score(
                benchmark.get("review_score_100")
            ),
            benchmark_bibliography_score=_optional_score(
                benchmark.get("bibliography_score_100")
            ),
            benchmark_strengths=tuple(
                str(value).strip() for value in strengths if str(value).strip()
            ),
            benchmark_hardware=str(
                benchmark.get("hardware") or benchmark_hardware
            ).strip(),
            download_url=str(raw.get("download_url") or "").strip(),
            sha256=str(raw.get("sha256") or "").strip().lower(),
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
        if spec.recommendation_rank is not None and spec.recommendation_rank <= 0:
            raise ValueError("model recommendation rank must be positive")
        if spec.download_priority is not None and spec.download_priority <= 0:
            raise ValueError("model download priority must be positive")
        if spec.benchmark_score is not None and not 0 <= spec.benchmark_score <= 100:
            raise ValueError("model benchmark score must be between 0 and 100")
        if not 0 <= spec.benchmark_success_count <= spec.benchmark_paper_count:
            raise ValueError("model benchmark success count is invalid")
        if (
            spec.benchmark_average_seconds is not None
            and (
                not math.isfinite(spec.benchmark_average_seconds)
                or spec.benchmark_average_seconds <= 0
            )
        ):
            raise ValueError("model benchmark average time must be positive")
        if (
            spec.benchmark_json_retries is not None
            and spec.benchmark_json_retries < 0
        ):
            raise ValueError("model benchmark retry count cannot be negative")
        if (
            spec.benchmark_bibliography_retries is not None
            and spec.benchmark_bibliography_retries < 0
        ):
            raise ValueError("model bibliography retry count cannot be negative")
        for score in (
            spec.benchmark_research_score,
            spec.benchmark_review_score,
            spec.benchmark_bibliography_score,
        ):
            if score is not None and not 0 <= score <= 100:
                raise ValueError("model benchmark detail score must be between 0 and 100")
        seen.add(spec.model_id)
        models.append(spec)
    return version, tuple(
        sorted(
            models,
            key=lambda item: (
                item.download_priority is None,
                item.download_priority or 0,
                item.recommendation_rank is None,
                item.recommendation_rank or 0,
                item.parameters_b,
            ),
        )
    )


def model_benchmark_summary(spec: ModelSpec) -> str:
    """Describe private real-paper benchmark results without exposing papers."""

    lines: list[str] = []
    if spec.recommendation_rank is not None:
        lines.append(f"★ 종합 추천 {spec.recommendation_rank}순위")
    facts: list[str] = []
    if spec.benchmark_paper_count:
        facts.append(
            f"실논문 {spec.benchmark_success_count}/{spec.benchmark_paper_count}편 완료"
        )
    if spec.benchmark_score is not None:
        facts.append(f"품질 {spec.benchmark_score:g}/100")
    if spec.benchmark_average_seconds is not None:
        facts.append(f"평균 {spec.benchmark_average_seconds:g}초")
    if spec.benchmark_json_retries is not None:
        facts.append(f"구조화 응답 재시도 {spec.benchmark_json_retries}회")
    if facts:
        lines.append(" · ".join(facts))
        if spec.benchmark_hardware:
            lines.append(f"측정 환경: {spec.benchmark_hardware}")
    elif spec.recommendation_rank is None:
        lines.append("실논문 벤치마크 미실시")
    if spec.benchmark_strengths:
        lines.append("강점: " + " · ".join(spec.benchmark_strengths))
    detail_scores: list[str] = []
    if spec.benchmark_research_score is not None:
        detail_scores.append(f"연구 {spec.benchmark_research_score:g}")
    if spec.benchmark_review_score is not None:
        detail_scores.append(f"리뷰 {spec.benchmark_review_score:g}")
    if spec.benchmark_bibliography_score is not None:
        detail_scores.append(f"서지 {spec.benchmark_bibliography_score:g}")
    if detail_scores:
        lines.insert(
            2 if len(lines) >= 2 else len(lines),
            "역할별 점수: " + " · ".join(detail_scores),
        )
    if spec.benchmark_bibliography_retries is not None:
        lines.append(
            f"서지정보 추가 요청 {spec.benchmark_bibliography_retries}회"
        )
    return "\n".join(lines)


def _optional_score(value: Any) -> float | None:
    return float(value) if value is not None else None


def model_usage_guidance(
    model_id: str,
    parameters_b: float | None = None,
    *,
    catalog_path: Path | None = None,
) -> ModelUsageGuidance:
    """Describe the safe product role of a local model without running it."""

    parameters = parameters_b if parameters_b and parameters_b > 0 else None
    if parameters is None:
        key = model_id.strip().casefold().removesuffix(":latest")
        try:
            _, specs = load_model_catalog(catalog_path)
            spec = next(
                (
                    item
                    for item in specs
                    if item.model_id.casefold().removesuffix(":latest") == key
                ),
                None,
            )
        except (OSError, ValueError, KeyError, TypeError):
            spec = None
        if spec is not None:
            parameters = spec.parameters_b
        else:
            match = re.search(
                r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?:\b|$)",
                model_id.casefold(),
            )
            parameters = float(match.group(1)) if match else None
    if parameters is None:
        return ModelUsageGuidance(
            role="직접 선택 모델",
            hallucination_risk="미확인",
            summary_strategy="모델 크기와 검증 결과를 확인한 뒤 사용",
            advanced_analysis=False,
            caution="카탈로그 밖 모델은 사양과 요약 안전성을 자동 보증하지 않습니다.",
        )
    normalized_model = model_id.strip().casefold().removesuffix(":latest")
    if normalized_model == "qwen3:1.7b":
        return ModelUsageGuidance(
            role="백그라운드 서지·Abstract",
            hallucination_risk="본문 종합 비권장",
            summary_strategy="서지정보 검증 후 Abstract만 정리",
            advanced_analysis=False,
            caution=(
                "본문 구역별 요약은 실행하지 않습니다. Abstract가 없으면 "
                "서지정보만 저장합니다."
            ),
        )
    if normalized_model == "qwen3.5:4b":
        return ModelUsageGuidance(
            role="수동 본문 분석 기본",
            hallucination_risk="주의 필요",
            summary_strategy="연구·리뷰 유형별 구역 요약 후 통합",
            advanced_analysis=False,
            caution=(
                "실논문 6편에서 Granite 4.1 3B보다 연구·리뷰·서지 정확도가 "
                "높았습니다. 중요한 수치와 결론은 원문 대조가 필요합니다."
            ),
        )
    if normalized_model == "granite4.1:3b":
        return ModelUsageGuidance(
            role="빠른 수동 요약 대안",
            hallucination_risk="주의 필요",
            summary_strategy="구역별 요약 후 통합",
            advanced_analysis=False,
            caution=(
                "Qwen3.5 4B보다 약 25% 빨랐지만 최신 실논문의 리뷰·서지 "
                "정확도는 낮았습니다. 속도를 우선할 때 선택하세요."
            ),
        )
    if parameters <= 0.8:
        return ModelUsageGuidance(
            role="벤치마크·분류 보조",
            hallucination_risk="매우 높음",
            summary_strategy="실사용 논문 요약 비권장",
            advanced_analysis=False,
            caution="서지정보 입력·사실 보존 비교용입니다. 중요한 논문 요약에는 사용하지 마세요.",
        )
    if parameters < 3:
        return ModelUsageGuidance(
            role="백그라운드 1~2B급",
            hallucination_risk="높음",
            summary_strategy="짧은 구역별 요약 후 축소 통합",
            advanced_analysis=False,
            caution=(
                "16GB급 PC의 상시 백그라운드 분석에 우선 권장합니다. "
                "정보 누락을 허용하는 대신 결과 확인이 필요합니다. "
                "최종 실패 시 Abstract·정규식 추출본만 표시합니다."
            ),
        )
    if parameters < 8:
        return ModelUsageGuidance(
            role="수동 정밀 3~4B급",
            hallucination_risk="주의 필요",
            summary_strategy="구역별 요약 후 통합",
            advanced_analysis=False,
            caution=(
                "일반 요약용 최소 권장 등급입니다. 수치·고유명사와 부정 표현은 "
                "원문 대조가 필요합니다."
            ),
        )
    return ModelUsageGuidance(
        role="고급 분석 8B+",
        hallucination_risk="상대적으로 낮지만 검증 필요",
        summary_strategy="정리된 전체 구역 직접 분석",
        advanced_analysis=True,
        caution="핵심 기여·한계를 포함합니다. 충분한 RAM과 긴 처리 시간을 요구합니다.",
    )


def recommend_models(
    hardware: HardwareProfile,
    ollama: OllamaRuntimeStatus,
    *,
    profile: str = "auto",
    selected_model: str = "",
    catalog_path: Path | None = None,
    installed_model_ids: Iterable[str] | None = None,
) -> ModelRecommendation:
    if profile not in VALID_MODEL_PROFILES:
        raise ValueError(f"unsupported model profile: {profile}")
    version, specs = load_model_catalog(catalog_path)
    installed_names = (
        _installed_aliases(ollama)
        if installed_model_ids is None
        else _model_aliases(installed_model_ids)
    )
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
        background_choice = next(
            (
                candidate
                for candidate in eligible
                if candidate.spec.model_id == BACKGROUND_RECOMMENDED_MODEL
            ),
            None,
        )
        if hardware.memory_total_gb < 24 and background_choice is not None:
            recommended = background_choice
        else:
            recommended = (
                _highest_stable(installed, hardware.memory_total_gb)
                if installed
                else _highest_stable(eligible, hardware.memory_total_gb)
            )
    elif profile == "speed":
        fast = [candidate for candidate in eligible if candidate.spec.parameters_b <= 4]
        recommended = next(
            (
                candidate
                for candidate in fast
                if candidate.spec.model_id == "granite4.1:3b"
            ),
            None,
        )
        recommended = (
            recommended
            or _highest_stable(fast, hardware.memory_total_gb)
            or _smallest(eligible)
        )
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
    if hardware.memory_total_gb < LOCAL_AI_MINIMUM_TOTAL_RAM_GB:
        rating = "비권장"
        warnings.append(
            "8GB급 PC에서는 로컬 AI 분석을 지원하지 않습니다. "
            "OpenAI·Claude API 또는 RAM 16GB 이상 PC를 권장합니다."
        )
    elif hardware.memory_total_gb < spec.minimum_ram_gb:
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
            spec.runtime_memory_gb + LOCAL_AI_SYSTEM_MEMORY_RESERVE_GB,
            hardware.memory_total_gb * 0.75,
        )
    ):
        rating = "권장"
        reasons.append("Windows와 sPDF용 메모리 여유를 남길 수 있습니다.")
    else:
        rating = "사용 가능"
        warnings.append("현재 가용 RAM에 따라 CPU 처리 속도가 느리거나 스왑이 생길 수 있습니다.")
    if (
        hardware.memory_available_gb
        < spec.runtime_memory_gb + LOCAL_AI_SYSTEM_MEMORY_RESERVE_GB
    ):
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


def memory_tier_guidance(total_ram_gb: float) -> str:
    """Explain the app's deliberately simple RAM policy for local models."""

    if total_ram_gb < LOCAL_AI_MINIMUM_TOTAL_RAM_GB:
        return (
            "8GB급 RAM: 내장 로컬 AI 분석은 지원하지 않습니다. "
            "OpenAI·Claude API 또는 RAM 16GB 이상 PC를 사용하세요."
        )
    if total_ram_gb < 24:
        return (
            "16GB급 RAM: 모델 실행 예상량 외에 시스템 여유 0.5GB를 남깁니다. "
            "백그라운드 분석은 검증된 Qwen3 1.7B를 권장합니다. "
            "Qwen3.5 2B는 우선 다운로드해 비교할 수 있습니다."
        )
    return (
        "24GB 이상 RAM: 모델 크기를 제한하지 않고 작업 목적과 품질에 맞게 "
        "직접 선택할 수 있습니다. Qwen3.5 2B·4B는 우선 다운로드 후보지만 "
        "벤치마크를 확인한 뒤 선택하세요."
    )


def recommendation_tier_overview() -> str:
    """Return the concise role split shown above per-model recommendations."""

    return (
        "용도별 권장: 백그라운드 서지·Abstract는 Qwen3 1.7B · "
        "수동 본문 분석 기본은 Qwen3.5 4B · "
        "Granite 4.1 3B는 속도 우선 대안 · "
        "Qwen3.5 2B와 Granite 3.3 2B는 비교 후보 · "
        "8B+는 기여·한계가 필요한 고급 분석용이며 모델명보다 "
        "메모리 적합도와 구조화 성공 여부를 우선합니다."
    )


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
    ranked = [
        candidate
        for candidate in pool
        if candidate.spec.recommendation_rank is not None
    ]
    if ranked:
        return min(
            ranked,
            key=lambda item: (
                item.spec.recommendation_rank or math.inf,
                -item.spec.quality,
            ),
        )
    return max(pool, key=lambda item: (item.spec.quality, item.spec.parameters_b))


def _smallest(candidates: list[ModelCandidate]) -> ModelCandidate | None:
    return min(candidates, key=lambda item: item.spec.parameters_b) if candidates else None


def _installed_aliases(ollama: OllamaRuntimeStatus) -> set[str]:
    return _model_aliases(model.name for model in ollama.models)


def _model_aliases(models: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for model in models:
        value = model.strip().casefold()
        if not value:
            continue
        names.add(value)
        if value.endswith(":latest"):
            names.add(value.removesuffix(":latest"))
    return names
