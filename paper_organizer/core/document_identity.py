"""PDF identity resilient to repository cover pages and small layout changes."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

import fitz

from paper_organizer.models.paper import (
    DocumentIdentity,
    DuplicateKind,
    DuplicateMatch,
    WrapperPage,
)


FINGERPRINT_VERSION = "paper-content-v1"
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s*)?\d+(?:\s+of\s+\d+)?\s*$", re.IGNORECASE)
_WORD_RE = re.compile(r"[\w-]+", re.UNICODE)
_ACADEMIC_MARKERS = ("abstract", "introduction", "keywords", "doi", "references")
_RESEARCHGATE_MARKERS = (
    "researchgate",
    "researchgate.net",
    "see discussions, stats, and author profiles",
    "citations",
    "reads",
)


class PdfIdentityError(RuntimeError):
    pass


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def extract_page_texts(path: Path) -> list[str]:
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise PdfIdentityError(f"PDF를 열 수 없습니다: {exc}") from exc
    try:
        if document.needs_pass:
            raise PdfIdentityError("암호화된 PDF는 동일성 분석 전에 잠금 해제가 필요합니다.")
        return [page.get_text("text") for page in document]
    finally:
        document.close()


def _normalized_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in unicodedata.normalize("NFKC", text).splitlines():
        line = _URL_RE.sub(" ", raw).casefold()
        line = re.sub(r"\s+", " ", line).strip()
        if not line or _PAGE_NUMBER_RE.match(line):
            continue
        lines.append(line)
    return lines


def normalize_page_text(text: str, repeated_lines: set[str] | None = None) -> str:
    repeated = repeated_lines or set()
    lines = [line for line in _normalized_lines(text) if line not in repeated]
    return " ".join(lines)


def find_repeated_lines(page_texts: Sequence[str]) -> set[str]:
    if len(page_texts) < 4:
        return set()
    counts: Counter[str] = Counter()
    for text in page_texts:
        counts.update(set(_normalized_lines(text)))
    threshold = max(3, math.ceil(len(page_texts) * 0.6))
    return {
        line
        for line, count in counts.items()
        if count >= threshold and len(line) <= 160
    }


def _looks_academic(text: str) -> bool:
    normalized = normalize_page_text(text)
    marker_count = sum(marker in normalized for marker in _ACADEMIC_MARKERS)
    word_count = len(_WORD_RE.findall(normalized))
    return marker_count >= 1 and word_count >= 40


def detect_wrapper_pages(page_texts: Sequence[str]) -> tuple[WrapperPage, ...]:
    wrappers: list[WrapperPage] = []
    for index, text in enumerate(page_texts[:3]):
        normalized = normalize_page_text(text)
        marker_count = sum(marker in normalized for marker in _RESEARCHGATE_MARKERS)
        next_is_academic = index + 1 < len(page_texts) and _looks_academic(page_texts[index + 1])
        current_is_academic = _looks_academic(text)
        if marker_count >= 2 and next_is_academic and not current_is_academic:
            confidence = min(0.99, 0.74 + marker_count * 0.07)
            wrappers.append(
                WrapperPage(
                    pdf_page=index + 1,
                    kind="researchgate_cover",
                    confidence=round(confidence, 2),
                )
            )
            continue
        if marker_count >= 3 and next_is_academic:
            wrappers.append(
                WrapperPage(
                    pdf_page=index + 1,
                    kind="repository_cover_candidate",
                    confidence=0.9,
                )
            )
    return tuple(wrappers)


def extract_doi(page_texts: Sequence[str]) -> str | None:
    for text in page_texts[:4]:
        match = _DOI_RE.search(text)
        if match:
            return match.group(0).rstrip(".,;)").casefold()
    return None


def _tokens(text: str) -> list[str]:
    return [token for token in _WORD_RE.findall(text.casefold()) if len(token) > 1]


def _shingles(tokens: Sequence[str], width: int = 5) -> Iterable[str]:
    if len(tokens) < width:
        yield " ".join(tokens)
        return
    for index in range(len(tokens) - width + 1):
        yield " ".join(tokens[index:index + width])


def _simhash(text: str) -> str:
    vector = [0] * 64
    counts = Counter(_shingles(_tokens(text)))
    for shingle, weight in counts.items():
        value = int.from_bytes(
            hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for bit in range(64):
            vector[bit] += weight if value & (1 << bit) else -weight
    result = 0
    for bit, score in enumerate(vector):
        if score >= 0:
            result |= 1 << bit
    return f"{result:016x}"


def _segment_text(text: str, count: int = 4) -> list[str]:
    tokens = _tokens(text)
    if not tokens:
        return [""]
    size = max(1, math.ceil(len(tokens) / count))
    return [" ".join(tokens[start:start + size]) for start in range(0, len(tokens), size)][:count]


def build_identity_from_pages(file_sha256: str, page_texts: Sequence[str]) -> DocumentIdentity:
    wrappers = detect_wrapper_pages(page_texts)
    wrapper_numbers = {page.pdf_page for page in wrappers if page.confidence >= 0.8}
    repeated_lines = find_repeated_lines(page_texts)
    content_pages = [
        normalize_page_text(text, repeated_lines)
        for page_number, text in enumerate(page_texts, start=1)
        if page_number not in wrapper_numbers
    ]
    content_text = " ".join(page for page in content_pages if page)
    segments = tuple(_simhash(segment) for segment in _segment_text(content_text))
    content_fingerprint = _simhash(content_text)
    doi = extract_doi(page_texts)
    work_id = f"doi:{doi}" if doi else f"content:{content_fingerprint}"
    source_variant = "researchgate" if any(
        page.kind == "researchgate_cover" for page in wrappers
    ) else "unknown"
    content_start = next(
        (page for page in range(1, len(page_texts) + 1) if page not in wrapper_numbers),
        1,
    )
    return DocumentIdentity(
        file_id=f"sha256:{file_sha256}",
        edition_id=f"sha256:{file_sha256}",
        work_id=work_id,
        file_sha256=file_sha256,
        content_fingerprint=content_fingerprint,
        segment_fingerprints=segments,
        fingerprint_version=FINGERPRINT_VERSION,
        doi=doi,
        source_variant=source_variant,
        wrapper_pages=wrappers,
        content_start_pdf_page=content_start,
        page_count=len(page_texts),
    )


def analyze_pdf_identity(path: Path) -> DocumentIdentity:
    return build_identity_from_pages(sha256_file(path), extract_page_texts(path))


def _fingerprint_similarity(left: str, right: str) -> float:
    if len(left) != 16 or len(right) != 16:
        return 0.0
    distance = (int(left, 16) ^ int(right, 16)).bit_count()
    return 1.0 - distance / 64


def compare_identities(left: DocumentIdentity, right: DocumentIdentity) -> DuplicateMatch:
    if left.file_sha256 == right.file_sha256:
        return DuplicateMatch(DuplicateKind.EXACT_FILE, 1.0, ("SHA-256 일치",))

    segment_pairs = list(zip(left.segment_fingerprints, right.segment_fingerprints))
    segment_scores = [_fingerprint_similarity(a, b) for a, b in segment_pairs]
    content_score = _fingerprint_similarity(
        left.content_fingerprint, right.content_fingerprint
    )
    average = sum(segment_scores) / len(segment_scores) if segment_scores else content_score
    minimum = min(segment_scores) if segment_scores else content_score
    reasons: list[str] = [f"본문 지문 평균 유사도 {average:.3f}"]

    if left.doi and right.doi and left.doi == right.doi:
        reasons.append("DOI 일치")
        if average >= 0.82:
            return DuplicateMatch(DuplicateKind.SAME_WORK, max(0.96, average), tuple(reasons))
        return DuplicateMatch(DuplicateKind.POSSIBLE_RELATED, average, tuple(reasons))

    if average >= 0.92 and minimum >= 0.84:
        reasons.append("여러 본문 구간이 모두 강하게 일치")
        return DuplicateMatch(DuplicateKind.SAME_WORK, average, tuple(reasons))
    if average >= 0.72 or content_score >= 0.82:
        reasons.append("일부 본문이 유사하여 판본 검토 필요")
        return DuplicateMatch(DuplicateKind.POSSIBLE_RELATED, average, tuple(reasons))
    return DuplicateMatch(DuplicateKind.DIFFERENT, average, tuple(reasons))
