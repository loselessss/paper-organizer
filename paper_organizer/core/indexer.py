"""Rebuild the compact library index from per-PDF sidecar JSON files."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SIDECAR_SUFFIX = ".paper.json"
INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class IndexProblem:
    path: str
    message: str


def iter_sidecars(library_root: Path) -> Iterable[Path]:
    papers = library_root / "papers"
    if not papers.is_dir():
        return ()
    return papers.rglob(f"*{SIDECAR_SUFFIX}")


def _nested(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
    return current


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def _normalize_search_text(values: Iterable[Any]) -> str:
    flattened: list[str] = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(str(item) for item in value)
        elif value:
            flattened.append(str(value))
    return " ".join(" ".join(flattened).casefold().split())


def _experimental_terms(record: dict[str, Any]) -> list[str]:
    details = record.get("experimental_details")
    if not isinstance(details, dict):
        return []
    terms: list[str] = []
    for value in details.values():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for key in ("name", "normalized_name"):
                        if item.get(key):
                            terms.append(str(item[key]))
                    terms.extend(_text_list(item.get("used_with")))
                    terms.extend(_text_list(item.get("supplements")))
                elif item:
                    terms.append(str(item))
    return terms


def load_sidecar(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sidecar root must be an object")
    identity = data.get("identity")
    if not isinstance(identity, dict) or not identity.get("work_id"):
        raise ValueError("identity.work_id is required")
    file_data = data.get("file")
    if not isinstance(file_data, dict) or not file_data.get("relative_path"):
        raise ValueError("file.relative_path is required")
    return data


def _variant(record: dict[str, Any]) -> dict[str, Any]:
    identity = record["identity"]
    file_data = record["file"]
    return {
        "file_id": identity.get("file_id", ""),
        "edition_id": identity.get("edition_id", ""),
        "relative_path": file_data.get("relative_path", ""),
        "source_variant": identity.get("source_variant", "unknown"),
        "size_bytes": file_data.get("size_bytes", 0),
        "page_count": file_data.get("page_count", 0),
    }


def _representative_priority(variant: dict[str, Any]) -> tuple[int, str]:
    ranking = {
        "publisher": 0,
        "author_accepted": 1,
        "preprint": 2,
        "unknown": 3,
        "researchgate": 4,
    }
    return ranking.get(str(variant.get("source_variant", "unknown")), 3), str(
        variant.get("relative_path", "")
    )


def build_library_index(
    records: Iterable[dict[str, Any]], *, generated_at: str | None = None
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["identity"]["work_id"]), []).append(record)

    works: list[dict[str, Any]] = []
    file_count = 0
    for work_id, variants_records in grouped.items():
        variants = sorted(
            (_variant(record) for record in variants_records),
            key=_representative_priority,
        )
        representative = variants[0]
        representative_record = next(
            record
            for record in variants_records
            if record["identity"].get("file_id") == representative["file_id"]
        )
        bibliography = representative_record.get("bibliography", {})
        classification = representative_record.get("classification", {})
        description = representative_record.get("description", {})
        experimental = _experimental_terms(representative_record)
        search_text = _normalize_search_text(
            (
                bibliography.get("title", ""),
                bibliography.get("authors", []),
                bibliography.get("year", ""),
                classification.get("category", ""),
                classification.get("subcategory", ""),
                classification.get("tags", []),
                description.get("keywords", []),
                description.get("summary_ko", ""),
                experimental,
            )
        )
        works.append(
            {
                "work_id": work_id,
                "title": bibliography.get("title", ""),
                "authors": _text_list(bibliography.get("authors")),
                "year": bibliography.get("year"),
                "category": classification.get("category", ""),
                "subcategory": classification.get("subcategory", ""),
                "tags": _text_list(classification.get("tags")),
                "summary_ko": description.get("summary_ko", ""),
                "experimental_terms": experimental,
                "representative_file_id": representative.get("file_id", ""),
                "variants": variants,
                "search_text": search_text,
            }
        )
        file_count += len(variants)

    works.sort(key=lambda item: (str(item.get("title", "")).casefold(), item["work_id"]))
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "work_count": len(works),
        "file_count": file_count,
        "works": works,
    }


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def rebuild_library_index(
    library_root: Path,
) -> tuple[dict[str, Any], list[IndexProblem]]:
    records: list[dict[str, Any]] = []
    problems: list[IndexProblem] = []
    for sidecar in iter_sidecars(library_root):
        try:
            records.append(load_sidecar(sidecar))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(IndexProblem(str(sidecar), str(exc)))
    index = build_library_index(records)
    _atomic_json_write(library_root / "index" / "library.json", index)
    _atomic_json_write(
        library_root / "index" / "errors.json",
        {
            "schema_version": 1,
            "generated_at": index["generated_at"],
            "problems": [asdict(problem) for problem in problems],
        },
    )
    return index, problems
