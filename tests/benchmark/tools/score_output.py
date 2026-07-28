#!/usr/bin/env python3
"""Score one synthetic benchmark summary with deterministic lexical checks."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUBRIC_PATH = BENCHMARK_ROOT / "scoring_rubric.json"
CLAIM_GROUPS = (
    "methods",
    "key_findings",
    "numeric_findings",
    "critical_negations",
)
NEGATION_TOKENS = frozenset(
    {"not", "no", "never", "neither", "nor", "without"}
)


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(
            r"[a-z0-9]+(?:[.-][a-z0-9]+)*%?",
            normalize(value),
        )
        if len(token) > 2
    ]


def _text_segments(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if parsed is not None:
        leaves: list[str] = []

        def collect(item: Any) -> None:
            if isinstance(item, Mapping):
                for child in item.values():
                    collect(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    collect(child)
            elif item is not None:
                leaves.append(str(item))

        collect(parsed)
        value = "\n".join(leaves)
    return [
        segment.strip()
        for segment in re.split(
            r"(?:[|\r\n]+|(?<=[.!?])\s+(?=[A-Z]))",
            value,
        )
        if segment.strip()
    ]


def token_overlap(claim: str, text: str) -> float:
    """Return the best local lexical match while preserving negation polarity."""
    claim_tokens = _tokens(claim)
    segments = [_tokens(segment) for segment in _text_segments(text)]
    if not claim_tokens or not any(segments):
        return 0.0
    best = 0.0
    expected_negation = bool(set(claim_tokens) & NEGATION_TOKENS)
    for text_tokens in segments:
        minimum_window = min(len(text_tokens), len(claim_tokens))
        maximum_window = min(len(text_tokens), len(claim_tokens) + 6)
        for window_size in range(minimum_window, maximum_window + 1):
            for start in range(max(1, len(text_tokens) - window_size + 1)):
                window = text_tokens[start : start + window_size]
                overlap = sum(
                    token in window for token in claim_tokens
                ) / len(claim_tokens)
                found_negation = bool(set(window) & NEGATION_TOKENS)
                if expected_negation != found_negation:
                    overlap *= 0.5
                best = max(best, overlap)
    return best


def load_rubric(path: Path | None = None) -> dict[str, Any]:
    rubric = json.loads(
        (path or DEFAULT_RUBRIC_PATH).read_text(encoding="utf-8")
    )
    weights = rubric.get("weights") or {}
    maximum = int(rubric.get("maximum_score") or 0)
    if sum(int(value) for value in weights.values()) != maximum:
        raise ValueError("벤치마크 배점 합계가 만점과 일치하지 않습니다.")
    return rubric


def _expected_values(ground_truth: Mapping[str, Any], field: str) -> list[str]:
    raw = ground_truth.get(field)
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(value) for value in raw if str(value).strip()]
    return []


def score_summary(
    ground_truth: Mapping[str, Any],
    output_text: str,
    *,
    rubric: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    active_rubric = dict(rubric or load_rubric())
    group_scores: dict[str, list[float]] = {}
    all_scores: list[float] = []
    for group in CLAIM_GROUPS:
        values = _expected_values(ground_truth, group)
        scores = [token_overlap(value, output_text) for value in values]
        group_scores[group] = scores
        all_scores.extend(scores)

    point_breakdown: dict[str, dict[str, float | int]] = {}
    raw_score = 0.0
    weights = dict(active_rubric["weights"])
    default_threshold = float(active_rubric["coverage_threshold"])
    for field, maximum_points in weights.items():
        values = _expected_values(ground_truth, field)
        threshold = (
            float(active_rubric["critical_negation_threshold"])
            if field == "critical_negations"
            else default_threshold
        )
        scores = [token_overlap(value, output_text) for value in values]
        matched = sum(score >= threshold for score in scores)
        earned = (
            float(maximum_points) * matched / len(values) if values else 0.0
        )
        raw_score += earned
        point_breakdown[field] = {
            "earned": round(earned, 2),
            "maximum": int(maximum_points),
            "matched_items": matched,
            "item_count": len(values),
            "threshold": threshold,
        }

    forbidden = [
        {
            "claim": str(claim),
            "overlap": token_overlap(str(claim), output_text),
        }
        for claim in ground_truth.get("forbidden_claims", [])
    ]
    forbidden_hits = sum(
        item["overlap"] >= active_rubric["forbidden_claim_threshold"]
        for item in forbidden
    )
    penalty_points = (
        forbidden_hits * active_rubric["forbidden_claim_penalty_each"]
    )
    final_score = max(0.0, raw_score - penalty_points)
    covered = sum(score >= default_threshold for score in all_scores)
    return {
        "document_id": str(ground_truth.get("document_id") or ""),
        "scoring_version": str(active_rubric["version"]),
        "score_100": round(final_score, 2),
        "raw_points": round(raw_score, 2),
        "penalty_points": round(float(penalty_points), 2),
        "point_breakdown": point_breakdown,
        "mean_token_coverage": (
            sum(all_scores) / len(all_scores) if all_scores else 0.0
        ),
        "covered_claims": covered,
        "claim_count": len(all_scores),
        "forbidden_hits": forbidden_hits,
        "group_scores": group_scores,
        "forbidden_claims": forbidden,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    ground_truth = json.loads(args.ground_truth.read_text(encoding="utf-8"))
    output_text = args.output.read_text(encoding="utf-8")
    score = score_summary(ground_truth, output_text)
    print(json.dumps(score, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
