#!/usr/bin/env python3
"""Score one synthetic benchmark summary with deterministic lexical checks."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping


CLAIM_GROUPS = (
    "methods",
    "key_findings",
    "numeric_findings",
    "critical_negations",
)


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def token_overlap(claim: str, text: str) -> float:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9.%-]+", normalize(claim))
        if len(token) > 2
    ]
    if not tokens:
        return 0.0
    normalized_text = normalize(text)
    return sum(token in normalized_text for token in tokens) / len(tokens)


def score_summary(
    ground_truth: Mapping[str, Any], output_text: str
) -> dict[str, Any]:
    group_scores: dict[str, list[float]] = {}
    all_scores: list[float] = []
    for group in CLAIM_GROUPS:
        values = [str(value) for value in ground_truth.get(group, [])]
        scores = [token_overlap(value, output_text) for value in values]
        group_scores[group] = scores
        all_scores.extend(scores)
    forbidden = [
        {
            "claim": str(claim),
            "overlap": token_overlap(str(claim), output_text),
        }
        for claim in ground_truth.get("forbidden_claims", [])
    ]
    forbidden_hits = sum(item["overlap"] >= 0.70 for item in forbidden)
    covered = sum(score >= 0.55 for score in all_scores)
    return {
        "document_id": str(ground_truth.get("document_id") or ""),
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
