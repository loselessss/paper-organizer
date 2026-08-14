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

DOCUMENT_TYPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["research_paper", "review_paper", "uncertain"],
        },
    },
    "required": ["document_type"],
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
    "short, bullet-ready points separated by blank lines, not as one wall of text. "
    "Each summary point should be one concise sentence or sentence-like fragment "
    "that can be displayed as a bullet. Do not include bullet characters, numbering, "
    "or markdown in the summary string. "
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
    "For patent title pages, including Korean KIPO documents, INID (54) is the "
    "invention title, (72) identifies inventors, and (43) or (45) supplies the "
    "publication year. "
    "authors or titles from cited references. ResearchGate, Academia.edu, PubMed, "
    "Google Scholar, Semantic Scholar, institutional repositories, publisher download "
    "banners, web domains, database names, patent offices, applicants, and assignees "
    "are distribution metadata, not a venue. Use empty values rather than guessing."
)

DOCUMENT_TYPE_INSTRUCTIONS = (
    "Classify the supplied title-page and Abstract excerpt only. Return review_paper "
    "only when the paper itself surveys, synthesizes, or meta-analyzes prior literature. "
    "Return research_paper when it reports its own experiment, observation, dataset, "
    "case, method evaluation, or other primary results. A sentence that merely says "
    "the paper reviews a topic is not sufficient if the Abstract describes new primary "
    "work. Ignore cited references and publisher or repository labels. Return uncertain "
    "when the excerpt does not contain enough evidence. Do not infer from outside knowledge."
)

PATENT_SUMMARY_INSTRUCTIONS = (
    " This document is a patent, not an academic paper. Analyze the disclosed "
    "invention using the description, claims, embodiments, examples, and drawings "
    "that are actually supplied. In summary, explain the technical field, prior "
    "technical problem, proposed solution, principal embodiments, and stated effects. "
    "Use research_question for the technical problem addressed by the invention. "
    "Use methods for disclosed construction, process steps, materials, conditions, "
    "and worked examples. Use contributions for claimed inventive concepts and stated "
    "technical effects, without making a legal conclusion about novelty, validity, "
    "infringement, or claim scope. Use limitations only for explicit constraints, "
    "dependencies, operating ranges, or conditions in the document. Never treat the "
    "applicant, assignee, patent office, examiner, or cited prior-art authors as "
    "inventors. Do not invent experimental validation that is not present."
)

REVIEW_SUMMARY_INSTRUCTIONS = (
    " This document is a review paper, not a primary research report. Summarize each "
    "section as synthesis of the literature, preserving the review's scope and cited "
    "evidence without inventing individual experiments. Use research_question for the "
    "review objective and scope; methods for an explicitly stated search or selection "
    "method, or otherwise for the concrete literature domains surveyed; summary for "
    "named systems, organisms, mechanisms, process relationships, applications, major "
    "themes, consensus, conflicts, and evidence strength; contributions for the review's integrative "
    "framework or conclusions; and limitations for explicit evidence gaps, bias, "
    "heterogeneity, and future research needs. Never label a review systematic or "
    "invent databases, date ranges, eligibility criteria, screening, or taxonomy unless "
    "the supplied text explicitly states them. Do not present cited studies' authors "
    "as this paper's authors or isolated cited findings as the review's own experiment."
)

RESEARCH_SUMMARY_INSTRUCTIONS = (
    " This document is a primary research paper. Put the tested question or hypothesis "
    "in research_question. Use methods for the essential experimental design: named "
    "subjects or materials, engineered constructs or treatments, controls or comparators, "
    "conditions, measurements, and analysis. In summary, prioritize the paper's own "
    "results over background and procedural detail. Preserve each distinct primary "
    "endpoint, exact value and unit, direction of change, fold or percentage comparison, "
    "time point, control or baseline, and supported application. Keep negative or null "
    "results and explicit caveats. Never promote an introduction claim, cited study, or "
    "authors' expectation into this paper's finding."
)

SEARCH_PLAN_INSTRUCTIONS = (
    "You prepare literal full-text searches for an academic paper library. "
    "Do not answer the question. Return 3 to 8 short search_queries, each one "
    "to four words, that are likely to occur verbatim in relevant papers. "
    "When the question is Korean, return separate Korean and English queries so "
    "English source papers remain searchable. Preserve identifiers such as gene "
    "and protein names, cell lines, media, reagents, model numbers, and acronyms "
    "exactly as written; never translate or normalize them. Avoid generic words "
    "such as paper, study, result, or "
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
    prompt_version: str = "research-summary-v11-direct"
    allowed_categories: tuple[str, ...] = ()
    context_window: int | None = None
    output_language: str = "ko"
    stage: str = "direct"
    json_retry: bool = False
    json_repair: bool = False
    language_retry: bool = False
    advanced_analysis: bool = True
    is_patent: bool = False
    document_type: str = "research_paper"

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
        if self.stage not in {"direct", "section", "synthesis", "translation", "abstract"}:
            raise ValueError(
                "stage must be direct, section, synthesis, abstract or translation"
            )
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
        if not isinstance(self.is_patent, bool):
            raise ValueError("is_patent must be a boolean")
        if self.document_type not in {"patent", "research_paper", "review_paper", "paper"}:
            raise ValueError("document_type is invalid")


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
class DocumentTypeRequest:
    document_text: str
    cloud_consent: bool = False
    max_output_tokens: int = 64
    context_window: int | None = None

    def validate(self) -> None:
        if not self.document_text.strip():
            raise ValueError("document_text cannot be empty")
        if not isinstance(self.cloud_consent, bool):
            raise ValueError("cloud_consent must be a boolean")
        if not 32 <= self.max_output_tokens <= 512:
            raise ValueError("max_output_tokens must be between 32 and 512")
        if (
            self.context_window is not None
            and not 4_096 <= self.context_window <= 262_144
        ):
            raise ValueError("context_window must be between 4096 and 262144")


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

    if request.stage == "translation":
        retry = (
            " The previous response did not contain a Korean translation. Translate "
            "the complete input again and obey every rule below."
            if request.language_retry
            else ""
        )
        return (
            "Translate the supplied academic analysis text into natural Korean. "
            "This is translation, not summarization: do not add, omit, infer, explain, "
            "or correct claims. Preserve every bracketed section heading, paragraph "
            "boundary, number, unit, negation, gene/protein name, cell line, culture "
            "medium, reagent, instrument model, acronym, and citation exactly where "
            "precision requires the source form. Return plain text only, with no JSON, "
            f"markdown fence, preface, or afterword.{retry}"
        )

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
        patent = (
            " This is a patent section. Preserve disclosed claim elements, process "
            "steps, embodiments, examples, operating ranges, and stated technical "
            "effects; do not reinterpret them as academic results."
            if request.is_patent
            else ""
        )
        review = (
            " This is a review-paper section. Prioritize concrete content over review "
            "labels: preserve named systems, organisms or populations, mechanisms, "
            "engineering or analytical strategies, process order and dependencies, "
            "target products or applications, and every separately stated central "
            "conclusion. Also preserve objective, scope, explicit selection or synthesis "
            "methods, conflicts, evidence strength, and gaps when present. Never call the "
            "review systematic or invent databases, criteria, screening, or taxonomy. "
            "Treat cited studies only as evidence synthesized by the review."
            if request.document_type == "review_paper"
            else ""
        )
        evidence_focus = (
            "the review's concrete subjects, mechanisms, strategies, relationships, "
            "applications, conclusions, explicit methods, conflicts, and gaps"
            if request.document_type == "review_paper"
            else "the tested question, essential design, named materials or subjects, "
            "controls, endpoints, complete result comparisons, exact numeric values and "
            "units, time points, and negations"
        )
        word_limit = 150 if request.document_type == "review_paper" else 160
        return (
            f"{language} Use only the supplied labeled paper section. Return plain "
            f"text only, not JSON or markdown. In at most {word_limit} words, preserve "
            f"{evidence_focus} that "
            "are actually present. Copy precise technical noun phrases and complete "
            "result comparisons rather than replacing them with generic prose. Do not "
            "infer from other sections. Ignore reference, bibliography, and works-cited "
            "entries. Ignore publisher headers, copyright or license text, DOI and web "
            "addresses, and received or accepted dates; these are not methods or evidence."
            f"{RESEARCH_SUMMARY_INSTRUCTIONS if request.document_type == 'research_paper' else ''}"
            f"{patent}{review}{retry}"
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
    if request.stage == "abstract":
        stage = (
            " This input contains only the paper's own Abstract. Rewrite that Abstract "
            "conservatively as one or two concise paragraphs without adding evidence, "
            "background, methods, results, limitations, or implications absent from it. "
            "Preserve every stated number, unit, comparison, negation, organism, material, "
            "and named method exactly. Put the rewritten Abstract only in summary as "
            "one or two short bullet-ready points without bullet characters. Return "
            "empty research_question, methods, keywords, and meta_tags; classification "
            "fields may still describe the Abstract's topic."
        )
    elif request.stage == "synthesis":
        if request.document_type == "review_paper":
            review_destination = (
                "Put integrative conclusions in contributions and explicit gaps, bias, "
                "heterogeneity, and future needs in limitations."
                if request.advanced_analysis
                else "The compact schema omits contributions and limitations, so preserve "
                "all concrete integrative conclusions and explicit evidence gaps in summary."
            )
            stage = (
                " This is the final pass over evidence summaries from a review paper. "
                "Reconcile them without replacing concrete evidence with generic prose. "
                "Put the objective and scope in research_question. In methods, include only "
                "an explicitly stated search or selection method; otherwise list the concrete "
                "literature domains surveyed without calling the review systematic, and make "
                "clear that this is literature synthesis rather than a new controlled experiment. "
                "In summary, write at least three evidence-dense bullet-ready points so separately stated "
                "major conclusions are not merged away. "
                "Preserve named systems, organisms or populations, mechanisms, strategies, "
                "process sequence, synergistic or integrated relationships, target applications, "
                "and each distinct major conclusion. "
                f"{review_destination} Never invent details absent from every section summary."
            )
        else:
            research_destination = (
                "Put supported contributions in contributions and explicit experimental "
                "limitations in limitations."
                if request.advanced_analysis
                else "The compact schema omits contributions and limitations, so preserve "
                "supported applications and explicit experimental limitations in summary."
            )
            stage = (
                " This is the final pass over evidence summaries from a primary research "
                "paper. Reconcile them into one coherent account without replacing precise "
                "evidence with generic prose. Put the tested question in research_question "
                "and the essential experimental design, subjects or materials, treatments, "
                "controls, conditions, and measurements in methods. Limit background to at "
                "most one sentence. In summary, state the central approach briefly and then "
                "write at least three evidence-dense result points before interpretation. "
                "Preserve every distinct primary endpoint and comparison separately, including "
                "exact values, units, fold or percentage changes, time points, controls, "
                "baselines, negative results, and whether evidence is in vitro, ex vivo, animal, "
                "or clinical. Distinguish measured results from discussion and proposed "
                f"applications. {research_destination} Never invent details omitted by every "
                "section summary or use cited studies as this paper's results."
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
        f"{json_repair}{language_retry}{analysis_scope}"
        f"{PATENT_SUMMARY_INSTRUCTIONS if request.is_patent and request.stage != 'abstract' else ''}"
        f"{REVIEW_SUMMARY_INSTRUCTIONS if request.document_type == 'review_paper' and request.stage != 'abstract' else ''}"
        f"{RESEARCH_SUMMARY_INSTRUCTIONS if request.document_type == 'research_paper' and request.stage != 'abstract' else ''}"
        f"{classification}"
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
class DocumentTypeData:
    document_type: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "DocumentTypeData":
        expected = set(DOCUMENT_TYPE_SCHEMA["required"])
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            extra = sorted(set(raw) - expected)
            raise ProviderError(
                f"Invalid document type fields; missing={missing}, extra={extra}"
            )
        document_type = raw["document_type"]
        allowed = set(DOCUMENT_TYPE_SCHEMA["properties"]["document_type"]["enum"])
        if not isinstance(document_type, str) or document_type not in allowed:
            raise ProviderError("Document type classification is invalid")
        return cls(document_type=document_type)


@dataclass(frozen=True, slots=True)
class DocumentTypeResult:
    provider: str
    model: str
    prompt_version: str
    data: DocumentTypeData
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

    def classify_document_type(
        self, request: DocumentTypeRequest
    ) -> DocumentTypeResult: ...

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


def parse_document_type_json(text: str) -> DocumentTypeData:
    raw = _parse_json_object(text, "document type")
    return DocumentTypeData.from_mapping(raw)


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
