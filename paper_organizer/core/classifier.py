# 학과 수준 taxonomy 키워드 스코어링으로 논문을 1차 분류하고 저널명을 추출하는 모듈
"""Regex/keyword first-pass classification before AI refinement.

분류는 3단계(정규식 → AI → 사람) 중 1단계다. 여기서 채운 값은
curation.field_sources에 "auto:regex"로 기록되어 이후 AI가 덮어쓸 수 있고,
사람이 수정하면 "user"로 잠긴다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

TAXONOMY_SCHEMA_VERSION = 1
DEFAULT_CATEGORY = "Uncategorized"
DEFAULT_SUBCATEGORY = "General"
_MINIMUM_SCORE = 4.0

_WORD_RE = re.compile(r"[a-z0-9가-힣][a-z0-9가-힣\-]*")

_VENUE_MARKER_RE = re.compile(
    r"(?i)\b(journal|transactions|proceedings|letters|review|reviews|reports|"
    r"advances|annals|acta|archives|bulletin|communications|nature|science|"
    r"cell|lancet|plos|bmc|ieee|acm|arxiv|biorxiv)\b"
)
_VENUE_CITATION_RE = re.compile(
    r"(?m)^\s*([A-Z][A-Za-z&.\-:()\s]{3,80}?)[,\s]+(?:vol\.?\s*)?\d{1,4}\s*"
    r"(?:\(\d{4}\)|,\s*\d{4})"
)
_VENUE_STOP_RE = re.compile(
    r"(?i)university|department|institute|hospital|correspond|e-?mail|"
    r"copyright|license|creative commons|downloaded|www\.|http|doi\.org|"
    r"see discussions|researchgate"
)


class TaxonomyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    category: str
    subcategory: str
    confidence: float
    matched_keywords: tuple[str, ...] = ()

    @property
    def classified(self) -> bool:
        return self.category != DEFAULT_CATEGORY


def default_taxonomy_path() -> Path:
    return Path(__file__).resolve().parents[1] / "models" / "taxonomy.json"


def load_taxonomy(path: Path | None = None) -> dict[str, Any]:
    source = path or default_taxonomy_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaxonomyError(f"분류 체계를 읽을 수 없습니다: {exc}") from None
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != TAXONOMY_SCHEMA_VERSION
        or not isinstance(data.get("categories"), list)
    ):
        raise TaxonomyError("지원하지 않는 분류 체계 형식입니다.")
    return data


def taxonomy_category_names(taxonomy: dict[str, Any] | None = None) -> list[str]:
    data = taxonomy or load_taxonomy()
    return [
        str(category.get("name", "")).strip()
        for category in data["categories"]
        if str(category.get("name", "")).strip()
    ]


def taxonomy_subcategory_names(
    category_name: str,
    taxonomy: dict[str, Any] | None = None,
) -> list[str]:
    data = taxonomy or load_taxonomy()
    target = category_name.strip().casefold()
    if not target:
        return []
    for category in data["categories"]:
        name = str(category.get("name", "")).strip()
        if name.casefold() != target:
            continue
        return [
            str(subcategory.get("name", "")).strip()
            for subcategory in category.get("subcategories", [])
            if str(subcategory.get("name", "")).strip()
        ]
    return []


def _keyword_hits(keywords: Sequence[str], zones: list[tuple[str, float]]) -> tuple[float, list[str]]:
    score = 0.0
    matched: list[str] = []
    for keyword in keywords:
        needle = keyword.casefold().strip()
        if not needle:
            continue
        keyword_score = 0.0
        for text, weight in zones:
            count = text.count(needle)
            if count:
                keyword_score += weight * min(count, 3)
        if keyword_score:
            score += keyword_score
            matched.append(keyword)
    return score, matched


def classify_text(
    title: str,
    page_texts: Sequence[str],
    *,
    taxonomy: dict[str, Any] | None = None,
    allowed_categories: Sequence[str] | None = None,
) -> ClassificationResult:
    """제목·앞부분 본문의 키워드 점수로 학과 수준 category를 고른다."""

    data = taxonomy or load_taxonomy()
    allowed = {name.casefold() for name in allowed_categories or [] if name.strip()}
    zones = [
        (" ".join(title.casefold().split()), 3.0),
        (" ".join(" ".join(page_texts[:2]).casefold().split()), 2.0),
        (" ".join(" ".join(page_texts[2:6]).casefold().split()), 1.0),
    ]
    best_name = DEFAULT_CATEGORY
    best_score = 0.0
    second_score = 0.0
    best_matched: list[str] = []
    best_entry: dict[str, Any] | None = None
    for category in data["categories"]:
        name = str(category.get("name", "")).strip()
        if not name:
            continue
        if allowed and name.casefold() not in allowed:
            continue
        score, matched = _keyword_hits(category.get("keywords", []), zones)
        if score > best_score:
            second_score = best_score
            best_score = score
            best_name = name
            best_matched = matched
            best_entry = category
        elif score > second_score:
            second_score = score
    if best_score < _MINIMUM_SCORE or best_entry is None:
        return ClassificationResult(DEFAULT_CATEGORY, DEFAULT_SUBCATEGORY, 0.0)
    subcategory = DEFAULT_SUBCATEGORY
    sub_best = 0.0
    for candidate in best_entry.get("subcategories", []):
        sub_name = str(candidate.get("name", "")).strip()
        if not sub_name:
            continue
        sub_score, _matched = _keyword_hits(candidate.get("keywords", []), zones)
        if sub_score > sub_best and sub_score >= 2.0:
            sub_best = sub_score
            subcategory = sub_name
    denominator = best_score + second_score
    confidence = round(best_score / denominator, 3) if denominator else 0.0
    return ClassificationResult(
        category=best_name,
        subcategory=subcategory,
        confidence=confidence,
        matched_keywords=tuple(best_matched[:12]),
    )


def extract_venue(page_texts: Sequence[str]) -> str:
    """첫 두 페이지의 헤더 줄에서 저널·학회명 후보를 찾는다."""

    candidates: list[tuple[float, str]] = []
    for page_number, text in enumerate(page_texts[:2]):
        lines = [" ".join(line.split()) for line in text.splitlines()]
        for line_number, line in enumerate(lines[:40]):
            if not 4 <= len(line) <= 90 or _VENUE_STOP_RE.search(line):
                continue
            match = _VENUE_CITATION_RE.match(line)
            if match:
                candidate = match.group(1).strip(" ,;:.-")
                if candidate:
                    candidates.append((3.0 - page_number, candidate))
                continue
            if _VENUE_MARKER_RE.search(line):
                letters = sum(char.isalpha() for char in line)
                digits = sum(char.isdigit() for char in line)
                if letters < 4 or digits > letters:
                    continue
                candidate = line.strip(" ,;:.-")
                weight = 2.0 - page_number - line_number * 0.01
                candidates.append((weight, candidate))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    venue = candidates[0][1]
    venue = re.sub(r"(?i)\s*(?:vol\.?|volume|no\.?|issue|pp\.?)\s*[\d\-–,()\s]*$", "", venue)
    venue = re.sub(r"[\d()\-–,;:]+$", "", venue).strip(" ,;:.-")
    return venue[:80]
