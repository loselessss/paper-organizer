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

SEARCH_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
        "year_from": {"type": "string"},
        "year_to": {"type": "string"},
    },
    "required": ["search_queries", "category", "year_from", "year_to"],
    "additionalProperties": False,
}

SEARCH_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer_ko": {"type": "string"},
        "papers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string"},
                    "pages": {"type": "array", "items": {"type": "integer"}},
                    "why": {"type": "string"},
                },
                "required": ["file_id", "pages", "why"],
                "additionalProperties": False,
            },
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
    },
    "required": ["answer_ko", "papers", "confidence"],
    "additionalProperties": False,
}

SYSTEM_INSTRUCTIONS = (
    "You analyze academic papers from section-labeled paragraph context. "
    "Use only the supplied document text. Keep Introduction, Materials and Methods, "
    "Results, and Discussion claims distinct. Treat REGEX-VALIDATED CANDIDATES as "
    "candidates that must still agree with the paper. Write summary_ko as three to "
    "five short paragraphs separated by blank lines, not as one wall of text. "
    "Preserve technical names accurately. "
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

SEARCH_PLAN_INSTRUCTIONS = (
    "You prepare literal full-text searches for an academic paper library. "
    "Do not answer the question. Return 3 to 8 short search_queries, each one "
    "to four words, that are likely to occur verbatim in relevant papers. "
    "Preserve technical names and include useful English equivalents when the "
    "question is Korean. Avoid generic words such as paper, study, result, or "
    "method. Extract a category or four-digit year bounds only when explicitly "
    "stated; otherwise return empty strings."
)

SEARCH_ANSWER_INSTRUCTIONS = (
    "Answer the user's question in Korean using only the supplied candidate "
    "paper context. Do not use outside knowledge. Every cited file_id must be "
    "copied exactly from the context and pages must be physical PDF page "
    "numbers shown there. If the evidence is insufficient, say so directly and "
    "use low confidence. Select only papers that materially support the answer. "
    "Reference, bibliography, and works-cited passages are discovery-only: never "
    "treat them as evidence for the candidate paper's findings."
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
    prompt_version: str = "paper-summary-v8-direct"
    allowed_categories: tuple[str, ...] = ()
    context_window: int | None = None
    output_language: str = "ko"
    stage: str = "direct"
    title_retry: bool = False
    advanced_analysis: bool = True

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
        if self.output_language not in {"ko", "source"}:
            raise ValueError("output_language must be ko or source")
        if self.stage not in {"direct", "section", "synthesis"}:
            raise ValueError("stage must be direct, section or synthesis")
        if not isinstance(self.title_retry, bool):
            raise ValueError("title_retry must be a boolean")
        if not isinstance(self.advanced_analysis, bool):
            raise ValueError("advanced_analysis must be a boolean")


@dataclass(frozen=True, slots=True)
class SearchPlanRequest:
    question: str
    cloud_consent: bool = False
    max_output_tokens: int = 800

    def validate(self) -> None:
        if not self.question.strip():
            raise ValueError("question cannot be empty")
        if len(self.question) > 2_000:
            raise ValueError("question cannot exceed 2000 characters")
        if not 128 <= self.max_output_tokens <= 4_000:
            raise ValueError("max_output_tokens must be between 128 and 4000")


@dataclass(frozen=True, slots=True)
class SearchPlanData:
    search_queries: tuple[str, ...]
    category: str = ""
    year_from: str = ""
    year_to: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SearchPlanData":
        expected = set(SEARCH_PLAN_SCHEMA["required"])
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            extra = sorted(set(raw) - expected)
            raise ProviderError(
                f"Invalid search plan fields; missing={missing}, extra={extra}"
            )
        queries = raw["search_queries"]
        if not isinstance(queries, list) or any(
            not isinstance(item, str) for item in queries
        ):
            raise ProviderError("Search plan queries must be a string array")
        values: dict[str, str] = {}
        for name in ("category", "year_from", "year_to"):
            if not isinstance(raw[name], str):
                raise ProviderError(f"Search plan field '{name}' must be a string")
            values[name] = raw[name]
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in queries:
            query = " ".join(value.split()).strip()
            key = query.casefold()
            if not query or key in seen:
                continue
            seen.add(key)
            cleaned.append(query)
            if len(cleaned) == 8:
                break
        return cls(search_queries=tuple(cleaned), **values)


@dataclass(frozen=True, slots=True)
class SearchPlanResult:
    provider: str
    model: str
    data: SearchPlanData


@dataclass(frozen=True, slots=True)
class SearchAnswerRequest:
    question: str
    context_text: str
    allowed_file_ids: tuple[str, ...]
    cloud_consent: bool = False
    max_output_tokens: int = 2_000
    context_window: int | None = None

    def validate(self) -> None:
        if not self.question.strip():
            raise ValueError("question cannot be empty")
        if not self.context_text.strip():
            raise ValueError("context_text cannot be empty")
        if not self.allowed_file_ids:
            raise ValueError("allowed_file_ids cannot be empty")
        if not 128 <= self.max_output_tokens <= 8_000:
            raise ValueError("max_output_tokens must be between 128 and 8000")
        if (
            self.context_window is not None
            and not 4_096 <= self.context_window <= 262_144
        ):
            raise ValueError("context_window must be between 4096 and 262144")


@dataclass(frozen=True, slots=True)
class SearchPaperEvidence:
    file_id: str
    pages: tuple[int, ...]
    why: str


@dataclass(frozen=True, slots=True)
class SearchAnswerData:
    answer_ko: str
    papers: tuple[SearchPaperEvidence, ...]
    confidence: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SearchAnswerData":
        expected = set(SEARCH_ANSWER_SCHEMA["required"])
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            extra = sorted(set(raw) - expected)
            raise ProviderError(
                f"Invalid search answer fields; missing={missing}, extra={extra}"
            )
        if not isinstance(raw["answer_ko"], str):
            raise ProviderError("Search answer must be a string")
        confidence = raw["confidence"]
        if confidence not in {"high", "medium", "low"}:
            raise ProviderError("Search confidence must be high, medium or low")
        raw_papers = raw["papers"]
        if not isinstance(raw_papers, list):
            raise ProviderError("Search answer papers must be an array")
        papers: list[SearchPaperEvidence] = []
        for item in raw_papers:
            if not isinstance(item, Mapping) or set(item) != {
                "file_id",
                "pages",
                "why",
            }:
                raise ProviderError("Search evidence has invalid fields")
            pages = item["pages"]
            if (
                not isinstance(item["file_id"], str)
                or not isinstance(item["why"], str)
                or not isinstance(pages, list)
                or any(
                    not isinstance(page, int)
                    or isinstance(page, bool)
                    or page < 1
                    for page in pages
                )
            ):
                raise ProviderError("Search evidence values are invalid")
            papers.append(
                SearchPaperEvidence(
                    file_id=item["file_id"],
                    pages=tuple(dict.fromkeys(pages)),
                    why=item["why"],
                )
            )
        return cls(
            answer_ko=raw["answer_ko"],
            papers=tuple(papers),
            confidence=confidence,
        )


@dataclass(frozen=True, slots=True)
class SearchAnswerResult:
    provider: str
    model: str
    data: SearchAnswerData


def system_instructions(request: SummaryRequest) -> str:
    """Append the caller's category list so the model picks from it."""

    language = (
        "Translate the explanatory fields, including summary_ko, research_question, "
        "methods, contributions, and limitations, into natural Korean. Keep established "
        "technical names in their source form where translation would reduce precision."
        if request.output_language == "ko"
        else "Keep the explanatory fields, including summary_ko, research_question, "
        "methods, contributions, and limitations, in the paper's original language. "
        "For an English paper, do not translate them into Korean."
    )
    stage = ""
    if request.stage == "section":
        stage = (
            " This is an intermediate pass over exactly one labeled paper section. "
            "Extract concise evidence from only that section. Keep numeric values and "
            "negations exact. Do not infer facts from other sections. Use empty values "
            "for bibliography or classification fields not visible in this section."
        )
    elif request.stage == "synthesis":
        stage = (
            " This is the final pass over JSON evidence summaries produced independently "
            "from paper sections. Reconcile them into one coherent paper summary. Preserve "
            "numeric values and negations, distinguish results from discussion, and never "
            "invent details omitted by every section summary."
        )
    retry = (
        " The previous response incorrectly translated or rewrote the paper title. "
        "Retry the complete JSON response, but copy title character-for-character "
        "from the source evidence in its original language. An English source title "
        "must contain no Korean translation."
        if request.title_retry
        else ""
    )
    analysis_scope = (
        ""
        if request.advanced_analysis
        else " Do not infer contributions or limitations; return empty arrays for both."
    )
    allowed = [name.strip() for name in request.allowed_categories if name.strip()]
    if not allowed:
        return f"{SYSTEM_INSTRUCTIONS} {language}{stage}{retry}{analysis_scope}"
    return (
        f"{SYSTEM_INSTRUCTIONS} {language}{stage}{retry}{analysis_scope} "
        "Choose category from exactly this list: "
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

    def plan_search(self, request: SearchPlanRequest) -> SearchPlanResult: ...

    def answer_search(self, request: SearchAnswerRequest) -> SearchAnswerResult: ...


def parse_summary_json(text: str) -> SummaryData:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("Provider returned invalid JSON") from exc
    if not isinstance(raw, dict):
        raise ProviderError("Provider summary must be a JSON object")
    return SummaryData.from_mapping(raw)


def parse_search_plan_json(text: str) -> SearchPlanData:
    raw = _parse_json_object(text, "search plan")
    return SearchPlanData.from_mapping(raw)


def parse_search_answer_json(text: str) -> SearchAnswerData:
    raw = _parse_json_object(text, "search answer")
    return SearchAnswerData.from_mapping(raw)


def _parse_json_object(text: str, label: str) -> Mapping[str, Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Provider returned invalid {label} JSON") from exc
    if not isinstance(raw, dict):
        raise ProviderError(f"Provider {label} must be a JSON object")
    return raw


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
