"""Classify supported patents and academic paper types from title-page text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

PATENT = "patent"
RESEARCH_PAPER = "research_paper"
REVIEW_PAPER = "review_paper"
PAPER_TYPES = frozenset({RESEARCH_PAPER, REVIEW_PAPER})


@dataclass(frozen=True, slots=True)
class DocumentTypeDecision:
    document_type: str
    patent_office: str = ""
    reason: str = ""


_US_NUMBER = re.compile(
    r"\bUS\s*(?:\d{4}\s*/\s*\d{4,7}|\d[\d,]{6,11})(?:\s*[A-Z]\d?)?\b",
    re.I,
)
_WO_NUMBER = re.compile(r"\bWO\s*\d{4}\s*/?\s*\d{4,6}\s*[A-Z]\d?\b", re.I)
_PCT_NUMBER = re.compile(r"\bPCT\s*/\s*[A-Z]{2}\s*\d{4}\s*/\s*\d{4,6}\b", re.I)
_KR_NUMBER = re.compile(r"(?:\b10\s*[-–]\s*\d{4}\s*[-–]\s*\d{4,7}\b|\b10\s*[-–]\s*\d{6,10}\b)")


def classify_document_type(page_texts: Iterable[str]) -> DocumentTypeDecision:
    pages = list(page_texts)
    front = "\n".join(pages[:2])[:30_000]
    folded = front.casefold()
    inid = bool(re.search(r"[\[(](?:10|11|19|43|45|54|72)[\])]", front))
    if _US_NUMBER.search(front) and (
        "united states patent" in folded
        or "us patent application" in folded
        or "patent application publication" in folded
        or inid
    ):
        return DocumentTypeDecision(PATENT, "US", "미국 특허 표제와 공개·등록번호 형식 확인")
    pct_heading = any(marker in folded for marker in (
        "world intellectual property organization", "patent cooperation treaty",
        "international application published under", "wipo",
    ))
    if (_WO_NUMBER.search(front) or _PCT_NUMBER.search(front)) and (pct_heading or inid):
        return DocumentTypeDecision(PATENT, "WIPO", "PCT/WO 표제와 국제공개번호 형식 확인")
    kr_heading = any(marker in front for marker in (
        "대한민국특허청", "공개특허공보", "등록특허공보", "특허청(KR)",
    ))
    if _KR_NUMBER.search(front) and (kr_heading or inid):
        return DocumentTypeDecision(PATENT, "KR", "한국 특허공보 표제와 번호 형식 확인")
    markers = (
        "systematic review", "scoping review", "narrative review", "literature review",
        "review article", "umbrella review", "meta-analysis", "meta analysis",
        "this review synthesizes", "this review examines", "this review summarizes",
        "체계적 문헌고찰", "범위 문헌고찰", "메타분석", "종설", "리뷰 논문",
    )
    if any(marker in folded for marker in markers):
        return DocumentTypeDecision(REVIEW_PAPER, reason="리뷰논문 표제·초록 표현 확인")
    if re.search(r"(?im)^\s*(?:review|review paper|review article)\s*$", front):
        return DocumentTypeDecision(REVIEW_PAPER, reason="첫 장의 리뷰논문 유형 표기 확인")
    return DocumentTypeDecision(RESEARCH_PAPER, reason="특허 고정 형식이 없어 연구논문으로 분류")
