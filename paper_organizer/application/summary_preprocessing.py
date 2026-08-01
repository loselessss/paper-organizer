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
    "results": r"results?(?:\s+(?:and|&)\s+discussion)?|findings|결과(?:\s*및\s*고찰)?",
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
_QUANTITATIVE_VALUE_RE = re.compile(
    r"(?<![\w.])(?:about|approximately|roughly|nearly|up\s+to|~|≈|[<>≤≥])?\s*"
    r"\d+(?:[.,]\d+)?\s*(?:[-‐‑–]\s*)?"
    r"(?:%|percent|percentage\s+points?|fold|times?|"
    r"(?:[fpnumkµμ]?g|mol|M|mM|µM|μM|nM|L|mL|µL|μL)(?:\s*/\s*[A-Za-z0-9µμ]+)?|"
    r"cfu(?:\s*/\s*[A-Za-z]+)?|cells?(?:\s*/\s*[A-Za-z]+)?|"
    r"h(?:ours?)?|min(?:utes?)?|s(?:econds?)?|days?|weeks?|months?|years?|°\s*C|℃)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_RESULT_CUE_RE = re.compile(
    r"\b(?:increase[ds]?|decrease[ds]?|reduce[ds]?|improve[ds]?|enhance[ds]?|"
    r"higher|lower|greater|less|more|versus|vs\.?|compared\s+(?:with|to)|"
    r"retain(?:ed|s)?|produce[ds]?|reach(?:ed|es)?|"
    r"achieve[ds]?|yield(?:ed|s)?|degrad(?:e[ds]?|ation)|decolori[sz](?:e[ds]?|ation)|"
    r"activity|efficiency|rate|concentration|level|amount|output|"
    r"증가|감소|향상|개선|저하|높|낮|대비|비교|생산|도달|유지|분해|활성|효율|농도)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)
_PROCEDURAL_SENTENCE_RE = re.compile(
    r"\b(?:to\s+(?:assess|measure|determine|evaluate|test)|"
    r"(?:was|were)\s+(?:performed|assessed|measured|stored|incubated|applied|used|"
    r"renewed|adjusted|filled)|"
    r"previous\s+(?:research|stud(?:y|ies)|work)|"
    r"측정하기\s+위해|평가하기\s+위해|실험을\s+위해)\b",
    re.IGNORECASE,
)
_PRIMARY_OUTCOME_RE = re.compile(
    r"\b(?:produc(?:e[ds]?|tion)|yield(?:ed|s)?|accumulat(?:e[ds]?|ion)|"
    r"degrad(?:e[ds]?|ation)|decolori[sz](?:e[ds]?|ation)|granules?|"
    r"survival|mortality|viability|efficacy|response|"
    r"생산(?:량)?|수율|축적|분해(?:율)?|탈색|생존|사망|효능|반응)\b",
    re.IGNORECASE,
)
_COMPARISON_CUE_RE = re.compile(
    r"\b(?:than|compared\s+(?:with|to)|versus|vs\.?|control|fold|while|"
    r"대비|비교|대조군|배)\b",
    re.IGNORECASE,
)
_FIGURE_CAPTION_RE = re.compile(
    r"^\s*(?:(?:supplementary|supporting)\s+)?"
    r"(?:fig(?:ure)?\.?|table)\s*[A-Z]?\d+(?:[.\-:]\d+)*\b"
    r"|^\s*(?:그림|도표|표)\s*\d+(?:[.\-:]\d+)*\b",
    re.IGNORECASE,
)
_PUBLISHER_PROOF_RE = re.compile(
    r"^\s*(?:journal\s+pre[- ]?proof|article\s+in\s+press|"
    r"uncorrected\s+(?:author'?s?\s+)?proof|proof\s+copy|"
    r"(?:author\s+)?accepted\s+manuscript|in\s+press|"
    r"this\s+is\s+(?:an?\s+)?(?:un)?edited\s+manuscript\s+accepted\s+for\s+publication.*|"
    r"please\s+cite\s+this\s+article\s+as\s*:?.*)\s*$",
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


def remove_publisher_proof_boilerplate(
    page_texts: Sequence[str],
) -> tuple[str, ...]:
    """Drop publisher proof watermarks without removing scientific uses of proof."""

    return tuple(
        "\n".join(
            line
            for line in str(text or "").splitlines()
            if not _PUBLISHER_PROOF_RE.fullmatch(line.strip())
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
    facts = _regex_facts(cleaned_pages, sections)
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
        if _PUBLISHER_PROOF_RE.fullmatch(line):
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


def _regex_facts(
    pages: Sequence[str], sections: Sequence[SectionContext]
) -> tuple[str, ...]:
    front = "\n".join(pages[:2])
    facts: list[str] = []
    dois = tuple(dict.fromkeys(match.group(0).rstrip(".,;") for match in _DOI_RE.finditer(front)))
    years = tuple(dict.fromkeys(_YEAR_RE.findall(front)))
    if dois:
        facts.append("DOI candidates: " + ", ".join(dois[:3]))
    if years:
        facts.append("Year candidates: " + ", ".join(years[:5]))
    quantitative_results = _quantitative_result_candidates(sections)
    if quantitative_results:
        facts.append(
            "Quantitative result candidates (verbatim; verify context): "
            + " | ".join(quantitative_results)
        )
    return tuple(facts)


def _quantitative_result_candidates(
    sections: Sequence[SectionContext], *, maximum: int = 8
) -> tuple[str, ...]:
    """Return evidence-like numeric sentences, excluding methods and references."""

    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    # Some two-column PDFs extract the abstract after an early Introduction
    # heading, so introduction remains eligible; explicit procedural and cited-
    # study cues below keep ordinary background measurements out.
    eligible = {
        "front", "abstract", "introduction", "results", "discussion", "conclusion"
    }
    for section in sections:
        if section.name not in eligible:
            continue
        for paragraph in section.paragraphs:
            sentences = re.split(
                r"(?<!Fig\.)(?<!fig\.)(?<!\bal\.)(?<=[.!?])\s+(?=[A-Z0-9가-힣(])",
                paragraph,
            )
            for sentence in sentences:
                value = " ".join(sentence.split()).strip()
                if not (25 <= len(value) <= 500):
                    continue
                if not _QUANTITATIVE_VALUE_RE.search(value):
                    continue
                if not _RESULT_CUE_RE.search(value):
                    continue
                if _PROCEDURAL_SENTENCE_RE.search(value):
                    continue
                if re.search(
                    r"\([A-Z][A-Za-z'’-]+(?:\s+et\s+al\.)?,?\s+(?:19|20)\d{2}\)",
                    value,
                ):
                    continue
                value = re.sub(r"\s*\[(?:\d+(?:\s*[-,]\s*\d+)*)\]\s*$", "", value)
                value = re.sub(
                    r"\s*\((?:supplementary\s+)?(?:Fig|Table)\.?\s*[A-Z]?\d+"
                    r"(?:[.\-:]\d+)*\)\.?\s*$",
                    "",
                    value,
                    flags=re.I,
                )
                value = re.sub(r"\s*\((?:Fig|Table)\.?\s*$", "", value, flags=re.I)
                if value in seen:
                    continue
                seen.add(value)
                priority = _quantitative_candidate_priority(section.name, value)
                candidates.append((priority, len(candidates), value))
    selected = sorted(candidates, key=lambda item: (-item[0], item[1]))[:maximum]
    selected.sort(key=lambda item: item[1])
    return tuple(value for _priority, _index, value in selected)


def _quantitative_candidate_priority(section_name: str, value: str) -> int:
    """Rank likely endpoints above setup, intermediate, and background values."""

    priority = 0
    if section_name in {"front", "abstract", "conclusion"}:
        priority += 4
    elif section_name == "results":
        priority += 1
    if _PRIMARY_OUTCOME_RE.search(value):
        priority += 4
    if _COMPARISON_CUE_RE.search(value):
        priority += 3
    if len(_QUANTITATIVE_VALUE_RE.findall(value)) >= 2:
        priority += 2
    if re.search(r"\b(?:no|not|without|did\s+not|없|않)\b", value, re.I):
        priority += 1
    if re.search(r"\bas\s+a\s+result\b", value, re.I):
        priority += 1
    if len(value) > 350:
        priority -= 1
    return priority


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
