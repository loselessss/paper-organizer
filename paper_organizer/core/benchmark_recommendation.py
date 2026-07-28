"""Recommend a local AI model from observed benchmark quality and speed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


PROFILE_WEIGHTS = {
    "eco": (0.50, 0.35, 0.15),
    "balanced": (0.70, 0.15, 0.15),
    "performance": (0.85, 0.05, 0.10),
}


@dataclass(frozen=True, slots=True)
class ObservedModelScore:
    model: str
    fit_score_100: float
    mean_quality_score_100: float
    mean_seconds: float
    speed_score_100: float
    success_rate: float
    successful_documents: int
    attempted_documents: int
    forbidden_hits: int
    eligible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservedModelRecommendation:
    profile: str
    recommended_model: str
    candidates: tuple[ObservedModelScore, ...]


def recommend_observed_model(
    results: Sequence[Mapping[str, Any]],
    *,
    profile: str = "balanced",
) -> ObservedModelRecommendation:
    """Rank models using measurements from the same document matrix."""
    if profile not in PROFILE_WEIGHTS:
        raise ValueError(f"지원하지 않는 벤치마크 추천 프로필입니다: {profile}")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for result in results:
        model = str(result.get("model") or "").strip()
        if model:
            grouped.setdefault(model, []).append(result)

    observations: list[dict[str, Any]] = []
    for model, model_rows in grouped.items():
        successes = [
            result
            for result in model_rows
            if result.get("status") == "ok"
            and isinstance((result.get("score") or {}).get("score_100"), (int, float))
        ]
        if not successes:
            continue
        observations.append(
            {
                "model": model,
                "attempted": len(model_rows),
                "successful": len(successes),
                "mean_quality": sum(
                    float(result["score"]["score_100"]) for result in successes
                )
                / len(successes),
                "mean_seconds": sum(
                    max(0.001, float(result["elapsed_seconds"]))
                    for result in successes
                )
                / len(successes),
                "forbidden_hits": sum(
                    int(result["score"].get("forbidden_hits") or 0)
                    for result in successes
                ),
            }
        )
    if not observations:
        return ObservedModelRecommendation(profile, "", ())

    fastest = min(item["mean_seconds"] for item in observations)
    quality_weight, speed_weight, reliability_weight = PROFILE_WEIGHTS[profile]
    candidates: list[ObservedModelScore] = []
    for item in observations:
        success_rate = item["successful"] / item["attempted"]
        speed_score = min(100.0, fastest / item["mean_seconds"] * 100.0)
        fit_score = (
            item["mean_quality"] * quality_weight
            + speed_score * speed_weight
            + success_rate * 100.0 * reliability_weight
        )
        eligible = success_rate >= 0.8 and item["mean_quality"] >= 50.0
        reasons = (
            f"논문별 평균 정확도 {item['mean_quality']:.1f}/100",
            f"평균 처리 시간 {item['mean_seconds']:.1f}초",
            f"완료율 {success_rate * 100:.0f}%",
            f"금지 주장 {item['forbidden_hits']}건",
        )
        candidates.append(
            ObservedModelScore(
                model=item["model"],
                fit_score_100=round(fit_score, 2),
                mean_quality_score_100=round(item["mean_quality"], 2),
                mean_seconds=round(item["mean_seconds"], 3),
                speed_score_100=round(speed_score, 2),
                success_rate=round(success_rate, 4),
                successful_documents=item["successful"],
                attempted_documents=item["attempted"],
                forbidden_hits=item["forbidden_hits"],
                eligible=eligible,
                reasons=reasons,
            )
        )
    candidates.sort(
        key=lambda item: (
            item.fit_score_100,
            item.mean_quality_score_100,
            -item.mean_seconds,
        ),
        reverse=True,
    )
    recommended = next((item.model for item in candidates if item.eligible), "")
    return ObservedModelRecommendation(profile, recommended, tuple(candidates))
