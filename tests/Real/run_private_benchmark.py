"""Run ignored private papers through installed Ollama models sequentially."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from paper_organizer.application.summary_service import (
    SummaryMode,
    prepare_summary,
    run_prepared_summary,
)
from paper_organizer.infra.settings import AppSettings
from tests.benchmark.tools.score_output import score_summary


ROOT = Path(__file__).resolve().parent
DEFAULT_MODELS = (
    "qwen3:0.6b",
    "qwen3:1.7b",
    "ministral-3:3b-instruct-2512-q4_K_M",
    "phi4-mini",
    "gemma3:4b-it-qat",
    "qwen3:8b",
)


class NoSecrets:
    def get(self, _provider: str) -> None:
        return None

    def set(self, _provider: str, _secret: str) -> None:
        raise RuntimeError("비공개 로컬 벤치마크는 API 키를 사용하지 않습니다.")

    def delete(self, _provider: str) -> None:
        return None


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                value,
                stream,
                ensure_ascii=False,
                indent=2,
                default=lambda item: (
                    str(item) if isinstance(item, Path) else repr(item)
                ),
            )
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


def safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value
    )


def unload_model(model: str) -> None:
    payload = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
    request = Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument(
        "--languages",
        nargs="+",
        choices=("source", "ko"),
        default=("source", "ko"),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--document-ids", nargs="+", default=())
    args = parser.parse_args()
    reference_path = ROOT / "work" / "reference.private.json"
    references: dict[str, dict] = {}
    review_rubric = json.loads(
        (REPOSITORY_ROOT / "tests" / "benchmark" / "review_scoring_rubric.json").read_text(
            encoding="utf-8"
        )
    )
    if reference_path.is_file():
        private_reference = json.loads(reference_path.read_text(encoding="utf-8"))
        references = {
            str(item["document_id"]): item
            for item in private_reference.get("documents", [])
        }

    manifest_path = ROOT / "work" / "manifest.private.json"
    if not manifest_path.is_file():
        raise SystemExit(
            "tests/Real/inspect_private_pdfs.py를 먼저 실행해 비공개 manifest를 만드세요."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    indexed_pdfs = [
        (str(row["document_id"]), ROOT / str(row["private_source"]))
        for row in manifest.get("documents", [])
        if (ROOT / str(row.get("private_source", ""))).is_file()
    ]
    if args.document_ids:
        wanted = {value.upper() for value in args.document_ids}
        indexed_pdfs = [
            item
            for item in indexed_pdfs
            if item[0].upper() in wanted
        ]
    if args.limit > 0:
        indexed_pdfs = indexed_pdfs[: args.limit]
    rows = []
    total = len(args.models) * len(indexed_pdfs) * len(args.languages)
    completed = 0
    for model in args.models:
        try:
            for document_id, pdf_path in indexed_pdfs:
                for language in args.languages:
                    completed += 1
                    result_path = (
                        ROOT
                        / "results"
                        / safe_name(model)
                        / language
                        / f"{document_id}.json"
                    )
                    if args.resume and result_path.is_file():
                        existing = json.loads(
                            result_path.read_text(encoding="utf-8")
                        )
                        if existing.get("status") == "ok":
                            rows.append(existing)
                            print(
                                f"[{completed}/{total}] 건너뜀 "
                                f"{model} {document_id} {language}",
                                flush=True,
                            )
                            continue
                    print(
                        f"[{completed}/{total}] 실행 "
                        f"{model} {document_id} {language}",
                        flush=True,
                    )
                    settings = AppSettings(
                        summary_provider="ollama",
                        selected_model=model,
                        summary_language=language,
                        resource_profile="balanced",
                        hardware_profile={},
                    )
                    started = time.perf_counter()
                    try:
                        prepared = prepare_summary(
                            pdf_path,
                            settings,
                            SummaryMode.FULL,
                        )
                        execution = run_prepared_summary(
                            prepared,
                            settings,
                            NoSecrets(),
                        )
                        result = {
                            "status": "ok",
                            "document_id": document_id,
                            "source_sha256": hashlib.sha256(
                                pdf_path.read_bytes()
                            ).hexdigest(),
                            "model": model,
                            "language": language,
                            "elapsed_seconds": round(
                                time.perf_counter() - started, 3
                            ),
                            "preview": asdict(prepared.preview),
                            "output": asdict(execution.result.data),
                            "provenance": execution.provenance,
                        }
                        reference = references.get(document_id, {}).get(language)
                        if isinstance(reference, dict):
                            result["score"] = score_summary(
                                reference,
                                json.dumps(result["output"], ensure_ascii=False),
                                rubric=(
                                    review_rubric
                                    if prepared.preview.document_type == "review_paper"
                                    else None
                                ),
                            )
                        result["paper_type"] = prepared.preview.document_type
                    except Exception as exc:
                        result = {
                            "status": "error",
                            "document_id": document_id,
                            "model": model,
                            "language": language,
                            "elapsed_seconds": round(
                                time.perf_counter() - started, 3
                            ),
                            "error": " ".join(str(exc).split()),
                        }
                    atomic_json(result_path, result)
                    rows.append(result)
                    print(
                        f"  {result['status']} "
                        f"{result['elapsed_seconds']:.1f}s"
                        + (
                            f" · {result['error']}"
                            if result["status"] == "error"
                            else ""
                        ),
                        flush=True,
                    )
        finally:
            unload_model(model)
    atomic_json(ROOT / "results" / "run.private.json", rows)
    failures = sum(row["status"] == "error" for row in rows)
    print(f"완료 {len(rows) - failures} · 실패 {failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
