#!/usr/bin/env python3
"""Run private papers by default, or the synthetic corpus when explicitly requested."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from paper_organizer.application.summary_service import (  # noqa: E402
    SummaryMode,
    prepare_summary,
    run_prepared_summary,
)
from paper_organizer.core.benchmark_recommendation import (  # noqa: E402
    recommend_observed_model,
)
from paper_organizer.infra.ollama_acceleration import (  # noqa: E402
    OLLAMA_IGPU_ENVIRONMENT,
)
from paper_organizer.infra.ollama_runtime import (  # noqa: E402
    OllamaRuntimeInspector,
)
from paper_organizer.infra.settings import (  # noqa: E402
    AppSettings,
    load_settings,
)

try:
    from .score_output import score_summary  # type: ignore[import-not-found]  # noqa: E402
except ImportError:
    from score_output import score_summary  # noqa: E402


DEFAULT_MODELS = (
    "qwen3:0.6b",
    "qwen3:1.7b",
    "granite4.1:3b",
    "ministral-3:3b-instruct-2512-q4_K_M",
    "phi4-mini",
    "qwen3:4b",
    "gemma3:4b-it-qat",
    "qwen3:8b",
)
BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
PRIVATE_RUNNER = REPOSITORY_ROOT / "tests" / "Real" / "run_private_benchmark.py"


def private_benchmark_command(
    models: list[str],
    *,
    language: str,
    resume: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(PRIVATE_RUNNER),
        "--models",
        *models,
        "--languages",
        language,
    ]
    if resume:
        command.append("--resume")
    return command


def apply_benchmark_acceleration(
    settings: AppSettings,
    environment: dict[str, str],
) -> str:
    """Apply the saved GPU-first policy to benchmark child processes."""

    if settings.ollama_force_igpu:
        environment[OLLAMA_IGPU_ENVIRONMENT] = "1"
        return "GPU 우선 (내장 GPU 포함, 실패 시 CPU)"
    environment.pop(OLLAMA_IGPU_ENVIRONMENT, None)
    return "Ollama 기본 장치 선택"


class _NoSecrets:
    def get(self, _provider: str) -> None:
        return None

    def set(self, _provider: str, _secret: str) -> None:
        raise RuntimeError("Ollama 벤치마크는 API 키를 사용하지 않습니다.")

    def delete(self, _provider: str) -> None:
        return None


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
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


def _atomic_text(path: Path, value: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding=encoding, newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _documents(requested: set[str]) -> list[dict[str, Any]]:
    manifest = json.loads(
        (BENCHMARK_ROOT / "manifest.json").read_text(encoding="utf-8")
    )
    documents = [
        item
        for item in manifest["documents"]
        if not str(item.get("difficulty", "")).startswith("ocr_")
    ]
    if requested:
        documents = [
            item for item in documents if item["document_id"] in requested
        ]
        missing = requested - {item["document_id"] for item in documents}
        if missing:
            raise ValueError(f"알 수 없는 문서 ID: {', '.join(sorted(missing))}")
    return documents


def _run_one(
    document: dict[str, Any],
    *,
    model: str,
    mode: SummaryMode,
    language: str,
    resource_profile: str,
) -> dict[str, Any]:
    pdf_path = BENCHMARK_ROOT / document["pdf"]
    truth_path = BENCHMARK_ROOT / document["ground_truth"]
    settings = AppSettings(
        summary_provider="ollama",
        selected_model=model,
        summary_language=language,
        resource_profile=resource_profile,
        hardware_profile={},
    )
    tracemalloc.start()
    started = time.perf_counter()
    try:
        prepared = prepare_summary(pdf_path, settings, mode)
        execution = run_prepared_summary(prepared, settings, _NoSecrets())
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        processor = _running_processor(model)
        output = asdict(execution.result.data)
        output_text = json.dumps(output, ensure_ascii=False)
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        score = score_summary(truth, output_text)
        return {
            "status": "ok",
            "document_id": document["document_id"],
            "title": document["title"],
            "difficulty": document["difficulty"],
            "model": model,
            "mode": mode.value,
            "language": language,
            "elapsed_seconds": round(elapsed, 3),
            "runner_peak_memory_mb": round(peak / (1024 * 1024), 2),
            "input_tokens": execution.result.input_tokens,
            "output_tokens": execution.result.output_tokens,
            "processor": processor,
            "preview": {
                "pages": list(prepared.preview.included_pdf_pages),
                "sections": list(prepared.preview.included_sections),
                "characters": prepared.preview.character_count,
                "estimated_input_tokens": prepared.preview.estimated_input_tokens,
                "truncated": prepared.preview.truncated,
                "summary_strategy": prepared.preview.summary_strategy,
            },
            "score": score,
            "output": output,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        return {
            "status": "error",
            "document_id": document["document_id"],
            "title": document["title"],
            "difficulty": document["difficulty"],
            "model": model,
            "mode": mode.value,
            "language": language,
            "elapsed_seconds": round(elapsed, 3),
            "runner_peak_memory_mb": round(peak / (1024 * 1024), 2),
            "processor": _running_processor(model),
            "error": " ".join(str(exc).split()),
        }
    finally:
        tracemalloc.stop()


def _running_processor(model: str) -> str:
    key = model.strip().casefold().removesuffix(":latest")
    status = OllamaRuntimeInspector().inspect()
    running = next(
        (
            item
            for item in status.running_models
            if item.name.casefold().removesuffix(":latest") == key
        ),
        None,
    )
    return running.processor if running is not None else "확인 안 됨"


def _write_comparison(
    output_dir: Path,
    rows: list[dict[str, Any]],
    *,
    recommendation_profile: str,
) -> None:
    recommendation = recommend_observed_model(
        rows,
        profile=recommendation_profile,
    )
    recommendation_by_model = {
        candidate.model: candidate for candidate in recommendation.candidates
    }
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=(
            "model",
            "document_id",
            "difficulty",
            "status",
            "elapsed_seconds",
            "runner_peak_memory_mb",
            "processor",
            "input_tokens",
            "output_tokens",
            "score_100",
            "bibliography_score_100",
            "bibliography_title",
            "bibliography_authors",
            "bibliography_year",
            "bibliography_venue",
            "raw_points",
            "penalty_points",
            "mean_token_coverage",
            "covered_claims",
            "claim_count",
            "forbidden_hits",
            "error",
        ),
    )
    writer.writeheader()
    for result in rows:
        score = result.get("score") or {}
        bibliography = score.get("bibliography") or {}
        bibliography_fields = bibliography.get("field_matches") or {}
        writer.writerow(
            {
                "model": result["model"],
                "document_id": result["document_id"],
                "difficulty": result["difficulty"],
                "status": result["status"],
                "elapsed_seconds": result["elapsed_seconds"],
                "runner_peak_memory_mb": result["runner_peak_memory_mb"],
                "processor": result.get("processor", ""),
                "input_tokens": result.get("input_tokens", ""),
                "output_tokens": result.get("output_tokens", ""),
                "score_100": score.get("score_100", ""),
                "bibliography_score_100": bibliography.get("score_100", ""),
                "bibliography_title": bibliography_fields.get("title", ""),
                "bibliography_authors": bibliography_fields.get("authors", ""),
                "bibliography_year": bibliography_fields.get("year", ""),
                "bibliography_venue": bibliography_fields.get("venue", ""),
                "raw_points": score.get("raw_points", ""),
                "penalty_points": score.get("penalty_points", ""),
                "mean_token_coverage": score.get("mean_token_coverage", ""),
                "covered_claims": score.get("covered_claims", ""),
                "claim_count": score.get("claim_count", ""),
                "forbidden_hits": score.get("forbidden_hits", ""),
                "error": result.get("error", ""),
            }
        )
    _atomic_text(
        output_dir / "comparison.csv",
        "\ufeff" + stream.getvalue(),
    )
    summary_stream = io.StringIO(newline="")
    summary_writer = csv.DictWriter(
        summary_stream,
        fieldnames=(
            "model",
            "success",
            "failed",
            "mean_seconds",
            "processor",
            "fit_score_100",
            "mean_score_100",
            "mean_coverage",
            "forbidden_hits",
        ),
    )
    summary_writer.writeheader()
    for model in dict.fromkeys(result["model"] for result in rows):
        model_rows = [result for result in rows if result["model"] == model]
        successes = [result for result in model_rows if result["status"] == "ok"]
        summary_writer.writerow(
            {
                "model": model,
                "success": len(successes),
                "failed": len(model_rows) - len(successes),
                "mean_seconds": (
                    round(
                        sum(result["elapsed_seconds"] for result in successes)
                        / len(successes),
                        3,
                    )
                    if successes
                    else ""
                ),
                "processor": ", ".join(
                    dict.fromkeys(
                        str(result.get("processor") or "")
                        for result in successes
                        if result.get("processor")
                    )
                ),
                "fit_score_100": (
                    recommendation_by_model[model].fit_score_100
                    if model in recommendation_by_model
                    else ""
                ),
                "mean_score_100": (
                    round(
                        sum(
                            result["score"]["score_100"]
                            for result in successes
                        )
                        / len(successes),
                        2,
                    )
                    if successes
                    else ""
                ),
                "mean_coverage": (
                    round(
                        sum(
                            result["score"]["mean_token_coverage"]
                            for result in successes
                        )
                        / len(successes),
                        4,
                    )
                    if successes
                    else ""
                ),
                "forbidden_hits": sum(
                    result["score"]["forbidden_hits"] for result in successes
                ),
            }
        )
    _atomic_text(
        output_dir / "model_summary.csv",
        "\ufeff" + summary_stream.getvalue(),
    )
    _atomic_json(
        output_dir / "recommendation.json",
        {
            "profile": recommendation.profile,
            "recommended_model": recommendation.recommended_model,
            "candidates": [asdict(item) for item in recommendation.candidates],
        },
    )
    paper_stream = io.StringIO(newline="")
    point_fields = (
        "title",
        "research_question",
        "methods",
        "key_findings",
        "numeric_findings",
        "critical_negations",
        "conclusion",
    )
    paper_writer = csv.DictWriter(
        paper_stream,
        fieldnames=(
            "model",
            "document_id",
            "difficulty",
            "title",
            "status",
            "score_100",
            *(f"{field}_points" for field in point_fields),
            "penalty_points",
            "forbidden_hits",
            "elapsed_seconds",
            "processor",
            "error",
        ),
    )
    paper_writer.writeheader()
    for result in rows:
        score = result.get("score") or {}
        breakdown = score.get("point_breakdown") or {}
        paper_writer.writerow(
            {
                "model": result["model"],
                "document_id": result["document_id"],
                "difficulty": result["difficulty"],
                "title": result.get("title", ""),
                "status": result["status"],
                "score_100": score.get("score_100", ""),
                **{
                    f"{field}_points": (
                        breakdown.get(field, {}).get("earned", "")
                    )
                    for field in point_fields
                },
                "penalty_points": score.get("penalty_points", ""),
                "forbidden_hits": score.get("forbidden_hits", ""),
                "elapsed_seconds": result["elapsed_seconds"],
                "processor": result.get("processor", ""),
                "error": result.get("error", ""),
            }
        )
    _atomic_text(
        output_dir / "paper_scores.csv",
        "\ufeff" + paper_stream.getvalue(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="비공개 사용자 논문 대신 저장소의 합성 논문을 사용합니다.",
    )
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--documents", nargs="*", default=[])
    parser.add_argument("--mode", choices=("quick", "full"), default="full")
    parser.add_argument("--language", choices=("ko", "source"), default="source")
    parser.add_argument(
        "--resource-profile",
        choices=("eco", "balanced", "performance"),
        default="balanced",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BENCHMARK_ROOT / "results",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="이미 성공 결과가 있는 모델/문서는 건너뜁니다.",
    )
    args = parser.parse_args()
    acceleration_policy = apply_benchmark_acceleration(
        load_settings(),
        os.environ,
    )
    print(f"가속 정책: {acceleration_policy}")
    if not args.synthetic:
        if not PRIVATE_RUNNER.is_file():
            parser.error(
                "기본 비공개 벤치마크 실행기가 없습니다. tests/Real에 사용자 "
                "논문과 run_private_benchmark.py를 두거나 --synthetic을 사용하세요."
            )
        command = private_benchmark_command(
            args.models,
            language=args.language,
            resume=args.resume,
        )
        return subprocess.run(
            command,
            cwd=str(REPOSITORY_ROOT),
            check=False,
        ).returncode
    documents = _documents(set(args.documents))
    output_dir = args.output.expanduser().resolve()
    rows: list[dict[str, Any]] = []
    total = len(args.models) * len(documents)
    completed = 0
    for model in args.models:
        safe_model = re_safe_name(model)
        for document in documents:
            completed += 1
            result_path = (
                output_dir / safe_model / f"{document['document_id']}.json"
            )
            if args.resume and result_path.is_file():
                existing = json.loads(result_path.read_text(encoding="utf-8"))
                if (
                    existing.get("status") == "ok"
                    and "score_100" in (existing.get("score") or {})
                ):
                    rows.append(existing)
                    print(f"[{completed}/{total}] 건너뜀 {model} {document['document_id']}")
                    continue
            print(f"[{completed}/{total}] 실행 {model} {document['document_id']}")
            result = _run_one(
                document,
                model=model,
                mode=SummaryMode(args.mode),
                language=args.language,
                resource_profile=args.resource_profile,
            )
            _atomic_json(result_path, result)
            rows.append(result)
            if result["status"] == "error":
                print(f"  실패: {result['error']}")
            else:
                score = result["score"]
                print(
                    "  완료 "
                    f"{result['elapsed_seconds']:.1f}s · "
                    f"{score['score_100']:.1f}/100 · "
                    f"coverage {score['mean_token_coverage']:.3f} · "
                    f"forbidden {score['forbidden_hits']}"
                )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_comparison(
        output_dir,
        rows,
        recommendation_profile=args.resource_profile,
    )
    _atomic_json(output_dir / "run.json", rows)
    failures = sum(result["status"] != "ok" for result in rows)
    print(f"결과: {output_dir / 'comparison.csv'}")
    print(f"성공 {len(rows) - failures} · 실패 {failures}")
    return 1 if failures else 0


def re_safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value
    )


if __name__ == "__main__":
    raise SystemExit(main())
