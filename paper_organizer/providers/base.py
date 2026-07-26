"""Common request, result and validation contracts for summary providers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from paper_organizer.infra.secrets import validate_api_key


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary_ko": {"type": "string"},
        "research_question": {"type": "string"},
        "methods": {"type": "array", "items": {"type": "string"}},
        "contributions": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "title": {"type": "string"},
        "authors": {"type": "array", "items": {"type": "string"}},
        "year": {"type": "string"},
        "venue": {"type": "string"},
        "category": {"type": "string"},
        "subcategory": {"type": "string"},
        "meta_tags": {"type": "array", "items": {"type": "string"}},
        "suggested_category": {"type": "string"},
    },
    "required": [
        "summary_ko",
        "research_question",
        "methods",
        "contributions",
        "limitations",
        "keywords",
        "title",
        "authors",
        "year",
        "venue",
        "category",
        "subcategory",
        "meta_tags",
        "suggested_category",
    ],
    "additionalProperties": False,
}

SYSTEM_INSTRUCTIONS = (
    "You analyze academic papers. Use only the supplied document text. "
    "Return Korean prose for summary_ko and preserve technical names accurately. "
    "If evidence is missing, use an empty string or empty list instead of guessing. "
    "Also correct the bibliography from the document: title is the paper's own "
    "title, authors are the listed authors, year is the four-digit publication "
    "year as a string, and venue is the journal or conference name. The extracted "
    "title may be inaccurate, so independently identify the exact title printed "
    "in the document. Return that title in its original language: an English paper "
    "must keep its English title. Never translate, romanize, summarize, or rewrite "
    "the title, author names, or venue; copy those fields verbatim from the source "
    "document, preserving spelling, word order, and punctuation. "
    "Do not summarize or analyze reference, bibliography, or works-cited entries, "
    "and never use them as evidence for the paper's findings or authorship. "
    "Every article type, including narrative reviews, systematic reviews, and "
    "meta-analyses, has a byline: extract all authors shown in that byline. Never "
    "treat a review article as authorless and never copy cited-reference authors. "
    "For patents, put inventors in authors and always return an empty venue; a "
    "patent office, applicant, assignee, or publication number is not a journal. "
    "Classify the paper into one university department-level category and a "
    "narrower subcategory, both written in Korean. Return about five concise, "
    "searchable meta_tags that describe the paper's topic, method, material, or "
    "application. Preserve established technical terms and do not use cited "
    "authors or reference titles as tags."
)

ApiKeySource = str | None | Callable[[], str | None]


class ProviderError(RuntimeError):
    """A safe, user-displayable provider failure without document contents."""


class CloudConsentRequiredError(ProviderError):
    pass


class JsonHttpClient(Protocol):
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    document_text: str
    cloud_consent: bool = False
    max_output_tokens: int = 2_000
    prompt_version: str = "paper-summary-v6"
    allowed_categories: tuple[str, ...] = ()
    context_window: int | None = None

    def validate(self) -> None:
        if not self.document_text.strip():
            raise ValueError("document_text cannot be empty")
        if not 128 <= self.max_output_tokens <= 32_000:
            raise ValueError("max_output_tokens must be between 128 and 32000")
        if (
            self.context_window is not None
            and not 4_096 <= self.context_window <= 262_144
        ):
            raise ValueError("context_window must be between 4096 and 262144")


def system_instructions(request: SummaryRequest) -> str:
    """Append the caller's category list so the model picks from it."""

    allowed = [name.strip() for name in request.allowed_categories if name.strip()]
    if not allowed:
        return SYSTEM_INSTRUCTIONS
    return (
        f"{SYSTEM_INSTRUCTIONS} Choose category from exactly this list: "
        f"{', '.join(allowed)}. If one fits, return it in category and return "
        "an empty suggested_category. If none fits, return empty category and "
        "subcategory strings and propose one concise Korean university "
        "department-level name in suggested_category. Never add a category on "
        "the user's behalf."
    )


@dataclass(frozen=True, slots=True)
class SummaryData:
    summary_ko: str
    research_question: str
    methods: tuple[str, ...]
    contributions: tuple[str, ...]
    limitations: tuple[str, ...]
    keywords: tuple[str, ...]
    title: str = ""
    authors: tuple[str, ...] = ()
    year: str = ""
    venue: str = ""
    category: str = ""
    subcategory: str = ""
    meta_tags: tuple[str, ...] = ()
    suggested_category: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SummaryData":
        expected = set(SUMMARY_SCHEMA["required"])
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            extra = sorted(set(raw) - expected)
            raise ProviderError(f"Invalid summary fields; missing={missing}, extra={extra}")
        strings: dict[str, str] = {}
        for name in (
            "summary_ko",
            "research_question",
            "title",
            "year",
            "venue",
            "category",
            "subcategory",
            "suggested_category",
        ):
            if not isinstance(raw[name], str):
                raise ProviderError(f"Summary field '{name}' must be a string")
            strings[name] = raw[name]
        arrays: dict[str, tuple[str, ...]] = {}
        for name in (
            "methods",
            "contributions",
            "limitations",
            "keywords",
            "authors",
            "meta_tags",
        ):
            value = raw[name]
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise ProviderError(f"Summary field '{name}' must be a string array")
            arrays[name] = tuple(value)
        return cls(**strings, **arrays)


@dataclass(frozen=True, slots=True)
class SummaryResult:
    provider: str
    model: str
    prompt_version: str
    data: SummaryData
    input_tokens: int | None = None
    output_tokens: int | None = None


class SummaryProvider(Protocol):
    name: str
    model: str
    is_cloud: bool

    def summarize(self, request: SummaryRequest) -> SummaryResult: ...


def parse_summary_json(text: str) -> SummaryData:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("Provider returned invalid JSON") from exc
    if not isinstance(raw, dict):
        raise ProviderError("Provider summary must be a JSON object")
    return SummaryData.from_mapping(raw)


def require_cloud_consent(request: SummaryRequest) -> None:
    if not request.cloud_consent:
        raise CloudConsentRequiredError(
            "Cloud summarization requires explicit consent to transmit document text"
        )


def require_api_key(api_key_source: ApiKeySource, provider: str) -> str:
    raw = api_key_source() if callable(api_key_source) else api_key_source
    try:
        return validate_api_key(provider, raw)
    except ValueError as exc:
        message = str(exc)
        if message == "API key cannot be empty":
            message = f"No {provider} API key is configured"
        raise ProviderError(message) from None
