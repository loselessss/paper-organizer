"""Common request, result and validation contracts for summary providers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from paper_organizer.infra.secrets import validate_api_key


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": 1_200},
        "research_question": {"type": "string"},
        "methods": {"type": "array", "items": {"type": "string"}},
        "contributions": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"},
        "subcategory": {"type": "string"},
        "meta_tags": {"type": "array", "items": {"type": "string"}},
        "suggested_category": {"type": "string"},
    },
    "required": [
        "summary",
        "research_question",
        "methods",
        "contributions",
        "limitations",
        "keywords",
        "category",
        "subcategory",
        "meta_tags",
        "suggested_category",
    ],
    "additionalProperties": False,
}

BIBLIOGRAPHY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "authors": {"type": "array", "items": {"type": "string"}},
        "year": {"type": "string"},
        "venue": {"type": "string"},
    },
    "required": ["title", "authors", "year", "venue"],
    "additionalProperties": False,
}

BASIC_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        name: definition
        for name, definition in SUMMARY_SCHEMA["properties"].items()
        if name not in {"contributions", "limitations"}
    },
    "required": [
        name
        for name in SUMMARY_SCHEMA["required"]
        if name not in {"contributions", "limitations"}
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
    "candidates that must still agree with the paper. Write summary as three to five "
    "short paragraphs separated by blank lines, not as one wall of text. "
    "Preserve technical names accurately. "
    "If evidence is missing, use an empty string or empty list instead of guessing. "
    "Do not summarize or analyze reference, bibliography, or works-cited entries, "
    "and never use them as evidence for the paper's findings or authorship. "
    "Return about five concise, searchable meta_tags that describe the paper's "
    "topic, method, material, or "
    "application. Preserve established technical terms and do not use cited "
    "authors or reference titles as tags."
)

BIBLIOGRAPHY_INSTRUCTIONS = (
    "Extract bibliographic identity only from the supplied first PDF page. Return "
    "the exact title in its original language, every byline author or patent "
    "inventor, the four-digit publication year, and the journal or conference name. "
    "Copy spelling and punctuation from the page; never translate, romanize, shorten, "
    "or rewrite these values. Reviews and meta-analyses still have authors. Never use "
    "authors or titles from cited references. ResearchGate, Academia.edu, PubMed, "
    "Google Scholar, Semantic Scholar, institutional repositories, publisher download "
    "banners, web domains, database names, patent offices, applicants, and assignees "
    "are distribution metadata, not a venue. Use empty values rather than guessing."
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
    prompt_version: str = "paper-summary-v9-direct"
    allowed_categories: tuple[str, ...] = ()
    context_window: int | None = None
    output_language: str = "ko"
    stage: str = "direct"
    json_retry: bool = False
    json_repair: bool = False
    language_retry: bool = False
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
        if not isinstance(self.json_retry, bool):
            raise ValueError("json_retry must be a boolean")
        if not isinstance(self.json_repair, bool):
            raise ValueError("json_repair must be a boolean")
        if self.json_retry and self.json_repair:
            raise ValueError("json_retry and json_repair cannot both be enabled")
        if not isinstance(self.language_retry, bool):
            raise ValueError("language_retry must be a boolean")
        if not isinstance(self.advanced_analysis, bool):
            raise ValueError("advanced_analysis must be a boolean")


@dataclass(frozen=True, slots=True)
class BibliographyRequest:
    document_text: str
    cloud_consent: bool = False
    max_output_tokens: int = 500
    prompt_version: str = "paper-bibliography-v1"
    context_window: int | None = None
    is_patent: bool = False
    retry: bool = False

    def validate(self) -> None:
        if not self.document_text.strip():
            raise ValueError("document_text cannot be empty")
        if not 128 <= self.max_output_tokens <= 4_000:
            raise ValueError("max_output_tokens must be between 128 and 4000")
        if (
            self.context_window is not None
            and not 4_096 <= self.context_window <= 262_144
        ):
            raise ValueError("context_window must be between 4096 and 262144")
        if not isinstance(self.is_patent, bool):
            raise ValueError("is_patent must be a boolean")
        if not isinstance(self.retry, bool):
            raise ValueError("retry must be a boolean")


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

    if request.stage == "section":
        language = (
            "Write the evidence summary in natural Korean."
            if request.output_language == "ko"
            else (
                "Write the evidence summary only in the paper's original language. "
                "For an English paper, write only English and no Korean translation."
            )
        )
        retry = (
            " The previous response used the wrong language. Rewrite the complete "
            "plain-text response in the requested language."
            if request.language_retry
            else ""
        )
        return (
            f"{language} Use only the supplied labeled paper section. Return plain "
            "text only, not JSON or markdown. In at most 120 words, preserve the "
            "research purpose, methods, findings, numeric values, and negations that "
            "are actually present. Do not infer from other sections. Ignore reference, "
            f"bibliography, and works-cited entries.{retry}"
        )

    if request.output_language == "ko":
        language = (
            "OUTPUT LANGUAGE CONTRACT — KOREAN: Write summary, research_question, "
            "methods, contributions, limitations, keywords, and meta_tags in natural "
            "Korean. Keep established technical names in their source form only when "
            "translation would reduce precision. Do not answer those fields in English."
        )
        language_reminder = (
            " FINAL LANGUAGE CHECK: The explanatory fields must be Korean."
        )
    else:
        language = (
            "OUTPUT LANGUAGE CONTRACT — ORIGINAL: Write summary, research_question, "
            "methods, contributions, limitations, keywords, and meta_tags only in the "
            "paper's original language. When the paper is English, all those fields "
            "must be English and must contain no Korean translation. Korean is permitted "
            "only in category, subcategory, and suggested_category."
        )
        language_reminder = (
            " FINAL LANGUAGE CHECK: For an English paper, every explanatory field must "
            "be English; do not output Korean outside the three classification fields."
        )
    stage = ""
    if request.stage == "synthesis":
        stage = (
            " This is the final pass over evidence summaries produced independently "
            "from paper sections. Reconcile them into one coherent paper summary. Preserve "
            "numeric values and negations, distinguish results from discussion, and never "
            "invent details omitted by every section summary."
        )
    json_retry = (
        " The previous response was not one complete valid JSON object. Retry "
        "the entire response using exactly the requested schema. Output JSON "
        "only: no markdown fence, commentary, prefix, suffix, or omitted field. "
        "Use empty strings or arrays when evidence is unavailable."
        if request.json_retry
        else ""
    )
    json_repair = ""
    if request.json_repair:
        schema = (
            SUMMARY_SCHEMA
            if request.advanced_analysis
            else BASIC_SUMMARY_SCHEMA
        )
        empty_value = {
            name: [] if definition.get("type") == "array" else ""
            for name, definition in schema["properties"].items()
        }
        json_repair = (
            " FINAL JSON RECOVERY MODE: Both earlier attempts failed JSON validation. "
            "Use a different response strategy: first decide each value silently, then "
            "serialize exactly one compact JSON object modeled on this type-correct "
            f"template: {json.dumps(empty_value, ensure_ascii=False, separators=(',', ':'))}. "
            "Replace template values only with evidence from the document. Keep every "
            "key exactly once, use double quotes, escape embedded quotes and line breaks, "
            "close every string, array, and object, and output nothing before or after "
            "the JSON object. Do not use markdown."
        )
    language_retry = (
        " The previous response violated the OUTPUT LANGUAGE CONTRACT. Rewrite the "
        "complete JSON response in the requested language. Do not reuse explanatory "
        "sentences written in the wrong language."
        if request.language_retry
        else ""
    )
    analysis_scope = (
        ""
        if request.advanced_analysis
        else " Do not infer contributions or limitations; those fields are omitted."
    )
    allowed = [name.strip() for name in request.allowed_categories if name.strip()]
    if allowed:
        classification = (
            " Choose category from exactly this Korean classification list: "
            f"{', '.join(allowed)}. If one fits, return it in category and return "
            "an empty suggested_category. If none fits, return empty category and "
            "subcategory strings and propose one concise Korean university "
            "department-level name in suggested_category. Never add a category on "
            "the user's behalf."
        )
    else:
        classification = (
            " Classify the paper into one Korean university department-level category "
            "and a narrower Korean subcategory."
        )
    return (
        f"{language} {SYSTEM_INSTRUCTIONS}{stage}{json_retry}"
        f"{json_repair}{language_retry}{analysis_scope}{classification}"
        f"{language_reminder}"
    )


def bibliography_instructions(request: BibliographyRequest) -> str:
    patent = (
        " This document is a patent. Put inventors in authors and always return an "
        "empty venue."
        if request.is_patent
        else ""
    )
    retry = (
        " The previous response contained missing or unverifiable values. Retry the "
        "complete JSON object and copy only values visibly printed on this page."
        if request.retry
        else ""
    )
    return f"{BIBLIOGRAPHY_INSTRUCTIONS}{patent}{retry}"


@dataclass(frozen=True, slots=True)
class BibliographyData:
    title: str
    authors: tuple[str, ...]
    year: str
    venue: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "BibliographyData":
        expected = set(BIBLIOGRAPHY_SCHEMA["required"])
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            extra = sorted(set(raw) - expected)
            raise ProviderError(
                f"Invalid bibliography fields; missing={missing}, extra={extra}"
            )
        for name in ("title", "year", "venue"):
            if not isinstance(raw[name], str):
                raise ProviderError(f"Bibliography field '{name}' must be a string")
        authors = raw["authors"]
        if not isinstance(authors, list) or any(
            not isinstance(author, str) for author in authors
        ):
            raise ProviderError("Bibliography field 'authors' must be a string array")
        return cls(
            title=raw["title"],
            authors=tuple(authors),
            year=raw["year"],
            venue=raw["venue"],
        )


@dataclass(frozen=True, slots=True)
class BibliographyResult:
    provider: str
    model: str
    prompt_version: str
    data: BibliographyData
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class SummaryData:
    summary: str
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
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        advanced_analysis: bool = True,
    ) -> "SummaryData":
        schema = SUMMARY_SCHEMA if advanced_analysis else BASIC_SUMMARY_SCHEMA
        expected = set(schema["required"])
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            extra = sorted(set(raw) - expected)
            raise ProviderError(f"Invalid summary fields; missing={missing}, extra={extra}")
        strings: dict[str, str] = {}
        for name in (
            "summary",
            "research_question",
            "category",
            "subcategory",
            "suggested_category",
        ):
            if not isinstance(raw[name], str):
                raise ProviderError(f"Summary field '{name}' must be a string")
            strings[name] = raw[name]
        arrays: dict[str, tuple[str, ...]] = {}
        array_names = ["methods", "keywords", "meta_tags"]
        if advanced_analysis:
            array_names[1:1] = ["contributions", "limitations"]
        for name in array_names:
            value = raw[name]
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise ProviderError(f"Summary field '{name}' must be a string array")
            arrays[name] = tuple(value)
        return cls(
            **strings,
            contributions=arrays.pop("contributions", ()),
            limitations=arrays.pop("limitations", ()),
            **arrays,
        )

    @classmethod
    def from_section_text(cls, text: str) -> "SummaryData":
        return cls(
            summary=text.strip(),
            research_question="",
            methods=(),
            contributions=(),
            limitations=(),
            keywords=(),
        )


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

    def extract_bibliography(
        self, request: BibliographyRequest
    ) -> BibliographyResult: ...

    def summarize(self, request: SummaryRequest) -> SummaryResult: ...

    def plan_search(self, request: SearchPlanRequest) -> SearchPlanResult: ...

    def answer_search(self, request: SearchAnswerRequest) -> SearchAnswerResult: ...


def summary_response_schema(request: SummaryRequest) -> Mapping[str, Any]:
    """Use the compact final schema when advanced fields are disabled."""

    return SUMMARY_SCHEMA if request.advanced_analysis else BASIC_SUMMARY_SCHEMA


def parse_summary_json(
    text: str,
    *,
    advanced_analysis: bool = True,
) -> SummaryData:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = _extract_json_object(text)
        if raw is None:
            raise ProviderError("Provider returned invalid JSON") from None
    if not isinstance(raw, dict):
        raise ProviderError("Provider summary must be a JSON object")
    return SummaryData.from_mapping(raw, advanced_analysis=advanced_analysis)


def parse_bibliography_json(text: str) -> BibliographyData:
    raw = _parse_json_object(text, "bibliography")
    return BibliographyData.from_mapping(raw)


def _extract_json_object(text: str) -> Mapping[str, Any] | None:
    """Recover one complete object wrapped in prose or a markdown fence."""
    decoder = json.JSONDecoder()
    for offset, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text, offset)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


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
