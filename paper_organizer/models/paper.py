"""Core immutable models for file, edition and work identity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class DuplicateKind(StrEnum):
    EXACT_FILE = "exact_file"
    SAME_WORK = "same_work"
    POSSIBLE_RELATED = "possible_related"
    DIFFERENT = "different"


@dataclass(frozen=True, slots=True)
class WrapperPage:
    pdf_page: int
    kind: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DocumentIdentity:
    file_id: str
    edition_id: str
    work_id: str
    file_sha256: str
    content_fingerprint: str
    segment_fingerprints: tuple[str, ...]
    fingerprint_version: str
    doi: str | None
    source_variant: str
    wrapper_pages: tuple[WrapperPage, ...]
    content_start_pdf_page: int
    page_count: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["segment_fingerprints"] = list(self.segment_fingerprints)
        data["wrapper_pages"] = [page.to_dict() for page in self.wrapper_pages]
        return data


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    kind: DuplicateKind
    score: float
    reasons: tuple[str, ...]

    @property
    def confirmed(self) -> bool:
        return self.kind in (DuplicateKind.EXACT_FILE, DuplicateKind.SAME_WORK)
