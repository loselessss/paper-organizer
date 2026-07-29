"""Build section-aware, OCR-tolerant context for paper summarization."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Sequence


SECTION_LABELS = {
    "front": "Front matter",
    "abstract": "Abstract",
    "introduction": "Introduction",
    "methods": "Materials and Methods",
    "results": "Results",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
}
SECTION_ORDER = tuple(SECTION_LABELS)
_SECTION_PATTERNS = {
    "abstract": r"abstract|summary|초록|요약",
    "introduction": r"introduction|background|서론|배경",
    "methods": (
        r"materials?\s*(?:and|&)\s*methods?|methods?(?:\s+and\s+materials?)?|"
        r"experimental(?:\s+procedures?)?|methodology|patients?\s+and\s+methods?|"
        r"재료\s*(?:및|와)\s*방법|연구\s*방법|실험\s*방법"
    ),
    "results": r"results?|findings|결과",
    "discussion": r"discussion|고찰|논의",
    "conclusion": r"conclusions?|concluding\s+remarks|결론",
    "references": (
        r"references?|bibliography|works\s+cited|literature\s+cited|참고문헌"
    ),
}
_HEADING_RE = re.compile(
    r"^\s*(?:(?:section\s+)?\d+(?:\.\d+)*[.)]?\s*)?"
    r"(?P<title>"
    + "|".join(f"(?P<{name}>{pattern})" for name, pattern in _SECTION_PATTERNS.items())
    + r")\s*[:.]?\s*$",
    re.IGNORECASE,
)
_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:"
    r"\[?\s*(?:pdf\s+)?(?:page|p\.?)\s*[:#.]?\s*\d{1,4}"
    r"(?:\s*(?:/|of)\s*\d{1,4})?\s*\]?"
    r"|(?:페이지|쪽)\s*[:#.]?\s*\d{1,4}"
    r"(?:\s*(?:/|중)\s*\d{1,4})?"
    r"|\d{1,4}\s*(?:/|of)\s*\d{1,4}"
    r"|[-–—]\s*\d{1,4}\s*[-–—]"
    r"|\d{1,4}"
    r")\s*$",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_FIGURE_CAPTION_RE = re.compile(
    r"^\s*(?:(?:supplementary|supporting)\s+)?"
    r"(?:fig(?:ure)?\.?|table)\s*[A-Z]?\d+(?:[.\-:]\d+)*\b"
    r"|^\s*(?:그림|도표|표)\s*\d+(?:[.\-:]\d+)*\b",
    re.IGNORECASE,
)
_GENERIC_DOCUMENT_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:open\s+access\s+)?(?:research|original|review|case|short|brief)\s+article"
    r"|article|open\s+access|research\s+paper|original\s+research"
    r")$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SectionContext:
    name: str
    label: str
    paragraphs: tuple[str, ...]
    pdf_pages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PreprocessedDocument:
    text: str
    sections: tuple[SectionContext, ...]
    included_pdf_pages: tuple[int, ...]
    regex_facts: tuple[str, ...]


def is_generic_document_heading(value: str) -> bool:
    """Return whether a heading describes the document type, not its title."""

    normalized = " ".join(str(value or "").split()).strip(" .:_-")
    return bool(
        normalized and _GENERIC_DOCUMENT_HEADING_RE.fullmatch(normalized)
    )


def remove_figure_and_table_captions(page_texts: Sequence[str]) -> tuple[str, ...]:
    """Drop figure and table captions from temporary AI summary input."""

    return tuple(
        "\n".join(
            line
            for line in str(text or "").splitlines()
            if not _FIGURE_CAPTION_RE.match(line)
        )
        for text in page_texts
    )


def preprocess_paper_text(
    page_texts: Sequence[str],
    *,
    page_numbers: Sequence[int] | None = None,
) -> PreprocessedDocument:
    """Remove page furniture, repair OCR text and retain scientific sections."""

    numbers = tuple(page_numbers or range(1, len(page_texts) + 1))
    if len(numbers) != len(page_texts):
        raise ValueError("page_numbers and page_texts must have equal length")
    repeated = _repeated_page_furniture(page_texts)
    cleaned_pages = tuple(
        _clean_page(text, repeated) for text in page_texts
    )
    cleaned_pages = _trim_reference_tail(cleaned_pages)
    sections = _detect_sections(cleaned_pages, numbers)
    facts = _regex_facts(cleaned_pages)
    if sections:
        included_pages = tuple(
            dict.fromkeys(page for section in sections for page in section.pdf_pages)
        )
        text = _render_context(sections, facts)
    else:
        fallback_paragraphs = _paragraphs("\n\n".join(cleaned_pages))
        fallback_pages = tuple(
            number for number, text in zip(numbers, cleaned_pages) if text.strip()
        )
        sections = (
            SectionContext(
                "document",
                "Document body",
                fallback_paragraphs,
                fallback_pages,
            ),
        )
        included_pages = fallback_pages
        text = _render_context(sections, facts)
    return PreprocessedDocument(text, sections, included_pages, facts)


def _repeated_page_furniture(page_texts: Sequence[str]) -> frozenset[str]:
    if len(page_texts) < 2:
        return frozenset()
    counts: Counter[str] = Counter()
    for text in page_texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        candidates = {*lines[:3], *lines[-3:]}
        counts.update(
            {
                _line_key(line)
                for line in candidates
                if len(line) <= 160 and _line_key(line)
            }
        )
    threshold = max(2, math.ceil(len(page_texts) * 0.5))
    return frozenset(key for key, count in counts.items() if count >= threshold)


def _line_key(line: str) -> str:
    value = unicodedata.normalize("NFKC", line)
    value = value.casefold()
    return " ".join(value.split()).strip(" -–—|")


def _clean_page(text: str, repeated: frozenset[str]) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = value.replace("\x00", " ").replace("\u00ad", "")
    value = (
        value.replace("ﬁ", "fi")
        .replace("ﬂ", "fl")
        .replace("ﬀ", "ff")
        .replace("ﬃ", "ffi")
        .replace("ﬄ", "ffl")
    )
    kept: list[str] = []
    for raw in value.splitlines():
        line = raw.strip()
        if not line or _PAGE_NUMBER_RE.fullmatch(line):
            kept.append("")
            continue
        if _line_key(line) in repeated:
            continue
        line = re.sub(r"(?<![\w.+-])@+(?![\w.-])", " ", line)
        line = re.sub(r"(?:[|~_^`]{2,}|[•·]{3,})", " ", line)
        line = "".join(
            char
            for char in line
            if char in "\t"
            or not unicodedata.category(char).startswith("C")
        )
        line = re.sub(r"[ \t]+", " ", line).strip()
        kept.append(line)
    value = "\n".join(kept)
    # Join words split only because OCR/PDF extraction wrapped at a hyphen.
    value = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _detect_sections(
    pages: Sequence[str], page_numbers: Sequence[int]
) -> tuple[SectionContext, ...]:
    captured: dict[str, list[tuple[int, str]]] = {
        name: [] for name in SECTION_ORDER
    }
    current = "front"
    saw_heading = False
    for page_number, text in zip(page_numbers, pages):
        for line in text.splitlines():
            match = _HEADING_RE.fullmatch(line.strip())
            if match:
                saw_heading = True
                name = next(
                    key for key in _SECTION_PATTERNS if match.group(key) is not None
                )
                if name == "references":
                    # A real trailing reference section was already removed by
                    # _trim_reference_tail; a surviving heading is usually a TOC line.
                    continue
                current = name
                continue
            if current and line.strip():
                captured[current].append((page_number, line.strip()))
    if not saw_heading:
        return ()
    contexts: list[SectionContext] = []
    for name in SECTION_ORDER:
        rows = captured[name]
        if not rows:
            continue
        paragraphs = _paragraphs("\n".join(text for _page, text in rows))
        if not paragraphs:
            continue
        contexts.append(
            SectionContext(
                name=name,
                label=SECTION_LABELS[name],
                paragraphs=paragraphs,
                pdf_pages=tuple(dict.fromkeys(page for page, _text in rows)),
            )
        )
    return tuple(contexts)


def _trim_reference_tail(pages: Sequence[str]) -> tuple[str, ...]:
    total = sum(len(page) for page in pages)
    offset = 0
    candidates: list[tuple[int, int, int]] = []
    for page_index, text in enumerate(pages):
        line_offset = 0
        for line in text.splitlines(keepends=True):
            match = _HEADING_RE.fullmatch(line.strip())
            if match and match.group("references") is not None:
                candidates.append((offset + line_offset, page_index, line_offset))
            line_offset += len(line)
        offset += len(text)
    minimum = max(500, int(total * 0.35))
    later_page = max(1, len(pages) // 2)
    usable = [
        candidate
        for candidate in candidates
        if candidate[0] >= minimum
        or (
            candidate[1] >= later_page
            and candidate[0] >= int(total * 0.35)
        )
    ]
    if not usable:
        return tuple(pages)
    _absolute, page_index, line_offset = usable[-1]
    trimmed = list(pages[: page_index + 1])
    trimmed[-1] = trimmed[-1][:line_offset].rstrip()
    return tuple(trimmed)


def _paragraphs(text: str, *, maximum_chars: int = 1_600) -> tuple[str, ...]:
    blocks = re.split(r"\n\s*\n", text)
    paragraphs: list[str] = []
    for block in blocks:
        value = " ".join(line.strip() for line in block.splitlines() if line.strip())
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            continue
        if len(value) <= maximum_chars:
            paragraphs.append(value)
            continue
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9가-힣])", value)
        chunk = ""
        for sentence in sentences:
            candidate = f"{chunk} {sentence}".strip()
            if chunk and len(candidate) > maximum_chars:
                paragraphs.append(chunk)
                chunk = sentence
            else:
                chunk = candidate
        if chunk:
            paragraphs.append(chunk)
    return tuple(paragraphs)


def _regex_facts(pages: Sequence[str]) -> tuple[str, ...]:
    front = "\n".join(pages[:2])
    facts: list[str] = []
    dois = tuple(dict.fromkeys(match.group(0).rstrip(".,;") for match in _DOI_RE.finditer(front)))
    years = tuple(dict.fromkeys(_YEAR_RE.findall(front)))
    if dois:
        facts.append("DOI candidates: " + ", ".join(dois[:3]))
    if years:
        facts.append("Year candidates: " + ", ".join(years[:5]))
    return tuple(facts)


def _render_context(
    sections: Sequence[SectionContext], facts: Sequence[str]
) -> str:
    output: list[str] = []
    if facts:
        output.append("[REGEX-VALIDATED CANDIDATES]\n" + "\n".join(facts))
    for section in sections:
        pages = ",".join(str(page) for page in section.pdf_pages)
        output.append(f"[SECTION: {section.label} | PDF PAGES: {pages}]")
        output.extend(
            f"[PARAGRAPH {index}]\n{paragraph}"
            for index, paragraph in enumerate(section.paragraphs, 1)
        )
    return "\n\n".join(output).strip()
