#!/usr/bin/env python3
"""Publish sanitized real-paper scores without private texts or model outputs."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULTS = REPOSITORY_ROOT / "tests" / "Real" / "results"
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "tests"
    / "benchmark"
    / "score_history"
    / "real_papers_v1.json"
)


def sanitized_runs(results_root: Path) -> list[dict[str, Any]]:
    """Return model runs containing scores and timings only."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(results_root.glob("*/REAL-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        model = str(raw.get("model") or "").strip()
        document_id = str(raw.get("document_id") or "").strip()
        if not model or not document_id.startswith("REAL-"):
            continue
        score = raw.get("score")
        score = score if isinstance(score, dict) else {}
        bibliography = score.get("bibliography")
        bibliography = (
            bibliography if isinstance(bibliography, dict) else {}
        )
        grouped.setdefault(model, []).append(
            {
                "paper_id": document_id,
                "paper_type": str(raw.get("difficulty") or "unknown"),
                "status": (
                    "ok" if raw.get("status") == "ok" else "error"
                ),
                "score_100": score.get("score_100"),
                "bibliography_score_100": bibliography.get("score_100"),
                "forbidden_hits": score.get("forbidden_hits"),
                "elapsed_seconds": raw.get("elapsed_seconds"),
                "processor": str(raw.get("processor") or ""),
                "input_tokens": raw.get("input_tokens"),
                "output_tokens": raw.get("output_tokens"),
            }
        )
    published: list[dict[str, Any]] = []
    for model, papers in sorted(grouped.items()):
        papers.sort(key=lambda item: item["paper_id"])
        successes = [item for item in papers if item["status"] == "ok"]
        scores = [
            float(item["score_100"])
            for item in successes
            if isinstance(item["score_100"], (int, float))
        ]
        times = [
            float(item["elapsed_seconds"])
            for item in successes
            if isinstance(item["elapsed_seconds"], (int, float))
        ]
        bibliography_scores = [
            float(item["bibliography_score_100"])
            for item in successes
            if isinstance(item["bibliography_score_100"], (int, float))
        ]
        research_scores = [
            float(item["score_100"])
            for item in successes
            if item["paper_type"] == "research"
            and isinstance(item["score_100"], (int, float))
        ]
        review_scores = [
            float(item["score_100"])
            for item in successes
            if item["paper_type"] == "review"
            and isinstance(item["score_100"], (int, float))
        ]
        published.append(
            {
                "model": model,
                "paper_count": len(papers),
                "success_count": len(successes),
                "mean_score_100": (
                    round(sum(scores) / len(scores), 2) if scores else None
                ),
                "research_mean_score_100": (
                    round(
                        sum(research_scores) / len(research_scores),
                        2,
                    )
                    if research_scores
                    else None
                ),
                "review_mean_score_100": (
                    round(sum(review_scores) / len(review_scores), 2)
                    if review_scores
                    else None
                ),
                "mean_bibliography_score_100": (
                    round(
                        sum(bibliography_scores)
                        / len(bibliography_scores),
                        2,
                    )
                    if bibliography_scores
                    else None
                ),
                "mean_elapsed_seconds": (
                    round(sum(times) / len(times), 3) if times else None
                ),
                "papers": papers,
            }
        )
    return published


def publish(
    results_root: Path,
    output: Path,
    *,
    hardware_label: str,
) -> Path:
    models = sanitized_runs(results_root)
    if not models:
        raise ValueError("공개할 실제 논문 벤치마크 점수가 없습니다.")
    value = {
        "schema_version": 1,
        "suite": "real-papers-v1",
        "privacy": (
            "PDF, title, ground truth, raw prompts and model outputs are "
            "intentionally excluded."
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conditions": {
            "paper_count": 4,
            "mode": "full",
            "language": "source",
            "resource_profile": "balanced",
            "hardware": hardware_label.strip() or "local GPU-first PC",
        },
        "models": models,
    }
    _atomic_json(output, value)
    return output


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--hardware",
        default="Intel Core i5-1334U / Intel Iris Xe / RAM 15.73GB",
    )
    args = parser.parse_args()
    output = publish(
        args.results.expanduser().resolve(),
        args.output.expanduser().resolve(),
        hardware_label=args.hardware,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
