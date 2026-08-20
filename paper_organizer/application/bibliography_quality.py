"""Bibliography source and confidence diagnostics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from paper_organizer.application.summary_preprocessing import (
    is_generic_document_heading,
)
from paper_organizer.core.document_type import PATENT


_DISTRIBUTION_PLATFORM_NAMES = (
    "ResearchGate",
    "Academia.edu",
    "PubMed",
    "PubMed Central",
    "PMC",
    "Google Scholar",
    "Semantic Scholar",
    "ScienceDirect",
    "SpringerLink",
    "Wiley Online Library",
    "Taylor & Francis Online",
    "bioRxiv",
    "medRxiv",
)
_PROSE_AUTHOR_RE = re.compile(
    r"\b(?:abstract|article|copyright|download|last\s+date|received|accepted|"
    r"published|corresponding|affiliation|university|department)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


@dataclass(frozen=True, slots=True)
class BibliographyFieldStatus:
    path: str
    label: str
    source: str
    source_label: str


@dataclass(frozen=True, slots=True)
class BibliographyQuality:
    level: str
    label: str
    fields: tuple[BibliographyFieldStatus, ...]
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "label": self.label,
            "fields": [
                {
                    "path": field.path,
                    "label": field.label,
                    "source": field.source,
                    "source_label": field.source_label,
                }
                for field in self.fields
            ],
            "issues": list(self.issues),
        }


def assess_bibliography_quality(record: Mapping[str, Any]) -> BibliographyQuality:
    """Return display-ready confidence and suspicious bibliography signals."""

    bibliography = record.get("bibliography")
    bibliography = bibliography if isinstance(bibliography, Mapping) else {}
    curation = record.get("curation")
    curation = curation if isinstance(curation, Mapping) else {}
    sources = curation.get("field_sources")
    sources = sources if isinstance(sources, Mapping) else {}
    document = record.get("document")
    document = document if isinstance(document, Mapping) else {}
    document_type = str(document.get("type") or "")

    fields = _field_statuses(sources, document_type=document_type)
    issues = _bibliography_issues(bibliography, sources, document_type=document_type)
    level, label = _confidence_level(fields, issues, document_type=document_type)
    return BibliographyQuality(
        level=level,
        label=label,
        fields=fields,
        issues=tuple(issues),
    )


def bibliography_quality_summary(record: Mapping[str, Any]) -> str:
    quality = assess_bibliography_quality(record)
    parts = [quality.label]
    source_text = " · ".join(
        f"{field.label} {field.source_label}" for field in quality.fields
    )
    if source_text:
        parts.append(source_text)
    if quality.issues:
        parts.append("확인: " + ", ".join(quality.issues))
    return " | ".join(parts)


def _field_statuses(
    sources: Mapping[str, Any],
    *,
    document_type: str,
) -> tuple[BibliographyFieldStatus, ...]:
    labels = (
        ("bibliography.title", "제목"),
        ("bibliography.authors", "발명자" if document_type == PATENT else "저자"),
        ("bibliography.year", "연도"),
        ("bibliography.venue", "저널/학회"),
    )
    if document_type == PATENT:
        labels = tuple(item for item in labels if item[0] != "bibliography.venue")
    return tuple(
        BibliographyFieldStatus(
            path=path,
            label=label,
            source=str(sources.get(path) or ""),
            source_label=source_label(sources.get(path)),
        )
        for path, label in labels
    )


def source_label(source: object) -> str:
    value = str(source or "").strip()
    lowered = value.casefold()
    if lowered == "user":
        return "직접입력"
    if lowered == "verified:crossref":
        return "Crossref"
    if lowered == "verified:pubmed":
        return "PubMed"
    if lowered.startswith("verified:"):
        return "외부"
    if lowered.startswith("ai:"):
        return "AI"
    if lowered.startswith("auto:regex"):
        return "정규식"
    if lowered.startswith("auto:"):
        return "자동"
    return "수동 확인 필요"


def _confidence_level(
    fields: tuple[BibliographyFieldStatus, ...],
    issues: list[str],
    *,
    document_type: str,
) -> tuple[str, str]:
    if issues:
        return "needs_review", "수동 확인 필요"
    required = (
        "bibliography.title",
        "bibliography.authors",
        "bibliography.year",
    )
    if document_type != PATENT:
        required = (*required, "bibliography.venue")
    relevant = [field for field in fields if field.path in required]
    sources = [field.source.casefold() for field in relevant]
    if sources and all(source == "user" for source in sources):
        return "certain", "확실"
    if any(source.startswith("verified:") for source in sources):
        return "external_verified", "외부 검증됨"
    if any(source.startswith("ai:") for source in sources):
        return "ai_estimated", "AI 추정"
    return "needs_review", "수동 확인 필요"


def _bibliography_issues(
    bibliography: Mapping[str, Any],
    sources: Mapping[str, Any],
    *,
    document_type: str,
) -> list[str]:
    issues: list[str] = []
    title = " ".join(str(bibliography.get("title") or "").split())
    authors = _string_list(bibliography.get("authors"))
    year = str(bibliography.get("year") or "").strip()
    venue = " ".join(str(bibliography.get("venue") or "").split())

    if not title:
        issues.append("제목 없음")
    elif is_generic_document_heading(title):
        issues.append("제목이 일반 머리말")

    author_label = "발명자" if document_type == PATENT else "저자"
    if not authors:
        issues.append(f"{author_label} 없음")
    else:
        suspicious_authors = [
            author for author in authors if _suspicious_author(author)
        ]
        if suspicious_authors:
            issues.append(f"{author_label} 값 의심")

    if not year:
        issues.append("연도 없음")
    elif not _YEAR_RE.fullmatch(year):
        issues.append("연도 형식 의심")
    elif (
        str(sources.get("bibliography.year") or "").startswith(("auto:", "ai:"))
        and not any(
            str(sources.get(path) or "").startswith("verified:")
            for path in (
                "bibliography.title",
                "bibliography.authors",
                "bibliography.year",
                "bibliography.venue",
            )
        )
        and (not authors or not venue)
        and document_type != PATENT
    ):
        issues.append("연도가 본문 인용연도일 수 있음")

    if document_type != PATENT:
        if not venue:
            issues.append("저널/학회 없음")
        elif _is_distribution_platform(venue):
            issues.append("저널/학회가 배포 플랫폼")
    return issues


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _suspicious_author(value: str) -> bool:
    text = " ".join(str(value or "").split()).strip(" ,;")
    if not text:
        return True
    if _PROSE_AUTHOR_RE.search(text):
        return True
    letters = re.findall(r"[A-Za-z가-힣]", text)
    if len(letters) <= 1:
        return True
    if len(text) <= 3 and not re.search(r"[가-힣]{2,}", text):
        return True
    return False


def _is_distribution_platform(value: str) -> bool:
    normalized = _normalize_bibliography_text(value)
    return any(
        _normalize_bibliography_text(name) in normalized
        for name in _DISTRIBUTION_PLATFORM_NAMES
    )


def _normalize_bibliography_text(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value.casefold(), flags=re.UNICODE).strip()
