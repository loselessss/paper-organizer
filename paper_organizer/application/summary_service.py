"""Prepare paper text and run the structured summary engine."""

from __future__ import annotations

import math
import json
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable

import fitz

from paper_organizer import __version__
from paper_organizer.application.ai_execution import (
    AI_PRIORITY_BACKGROUND,
    AI_PRIORITY_MANUAL,
    AiExecutionQueue,
    global_ai_execution_queue,
)
from paper_organizer.core.classifier import TaxonomyError, taxonomy_category_names
from paper_organizer.core.document_type import (
    PATENT,
    RESEARCH_PAPER,
    REVIEW_PAPER,
    classify_document_type,
    detect_document_bundle,
)
from paper_organizer.core.document_identity import detect_wrapper_pages
from paper_organizer.application.summary_preprocessing import (
    PreprocessedDocument,
    is_generic_document_heading,
    preprocess_paper_text,
    remove_figure_and_table_captions,
    remove_publisher_proof_boilerplate,
)
from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import AppSettings
from paper_organizer.infra.settings import (
    default_settings_path,
    load_settings,
    settings_for_summary_purpose,
)
from paper_organizer.providers.base import (
    BibliographyData,
    BibliographyRequest,
    BibliographyResult,
    DocumentTypeRequest,
    JsonHttpClient,
    ProviderError,
    SummaryData,
    SummaryProvider,
    SummaryRequest,
    SummaryResult,
)
from paper_organizer.providers.registry import build_provider


QUICK_MAX_CHARS = 30_000
FULL_MAX_CHARS = 120_000
MINIMUM_TEXT_CHARS = 500
CONTEXT_TOKEN_RESERVE = 3_000
OCR_MINIMUM_OLLAMA_PARAMETERS_B = 8.0
_BIBLIOGRAPHY_MAX_CHARS = 12_000
_DISTRIBUTION_PLATFORM_NAMES = (
    "researchgate",
    "academia.edu",
    "pubmed",
    "google scholar",
    "semantic scholar",
    "sciencedirect",
    "springerlink",
    "wiley online library",
    "institutional repository",
)
_REFERENCE_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:(?:\d+(?:\.\d+)*)[.)]?[ \t]+)?"
    r"(?:references?(?:[ \t]+(?:and[ \t]+notes|and[ \t]+further[ \t]+reading|cited))?|bibliography|"
    r"works[ \t]+cited|literature[ \t]+cited|참고문헌)"
    r"[ \t]*$"
)


class SummaryPreparationError(RuntimeError):
    pass


class SummaryRetryExhaustedError(ProviderError):
    """A validated summary still failed after every bounded retry."""

    def __init__(
        self,
        message: str,
        *,
        failure_kind: str,
        attempts: int,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.attempts = attempts


class SummaryMode(StrEnum):
    QUICK = "quick"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class SummaryPreview:
    pdf_path: Path
    mode: SummaryMode
    provider: str
    model: str
    page_count: int
    included_pdf_pages: tuple[int, ...]
    character_count: int
    estimated_input_tokens: int
    truncated: bool
    sends_to_cloud: bool
    requires_cloud_consent: bool
    context_window: int | None = None
    included_sections: tuple[str, ...] = ()
    output_language: str = "ko"
    summary_strategy: str = "direct"
    document_type: str = "research_paper"
    document_type_source: str = "auto:regex"


@dataclass(frozen=True, slots=True)
class RegexSummaryFallback:
    """Deterministic source excerpts retained when AI summarization fails."""

    abstract: str = ""
    abstract_pdf_pages: tuple[int, ...] = ()
    facts: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.abstract.strip() or self.facts)


@dataclass(frozen=True, slots=True)
class PreparedSummary:
    preview: SummaryPreview
    document_text: str = field(repr=False)
    section_contexts: tuple[str, ...] = field(default=(), repr=False)
    bibliography_text: str = field(default="", repr=False)
    document_type_text: str = field(default="", repr=False)
    requires_document_type_confirmation: bool = False
    regex_fallback: RegexSummaryFallback = field(
        default_factory=RegexSummaryFallback,
        repr=False,
    )
    patent_claims_text: str = field(default="", repr=False)


@dataclass(frozen=True, slots=True)
class SummaryExecution:
    preview: SummaryPreview
    result: SummaryResult
    json_retry_count: int = 0
    language_retry_count: int = 0
    bibliography_retry_count: int = 0
    bibliography_status: str = "ok"
    bibliography_verified_fields: tuple[str, ...] = ()
    patent_claims_text: str = field(default="", repr=False)

    @property
    def provenance(self) -> dict[str, object]:
        return {
            "app_version": __version__,
            "provider": self.result.provider,
            "model": self.result.model,
            "prompt_version": self.result.prompt_version,
            "analysis_level": self.preview.mode.value,
            "input_tokens": self.result.input_tokens,
            "output_tokens": self.result.output_tokens,
            "context_window": self.preview.context_window,
            "included_sections": list(self.preview.included_sections),
            "output_language": self.preview.output_language,
            "summary_strategy": self.preview.summary_strategy,
            "document_type": self.preview.document_type,
            "document_type_source": self.preview.document_type_source,
            "json_retry_count": self.json_retry_count,
            "language_retry_count": self.language_retry_count,
            "bibliography_retry_count": self.bibliography_retry_count,
            "bibliography_status": self.bibliography_status,
            "bibliography_verified_fields": list(
                self.bibliography_verified_fields
            ),
        }


class SummaryController:
    """Run the shared summary engine without exposing the secret store."""

    def __init__(
        self,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        http_client: JsonHttpClient | None = None,
        ollama_starter: Callable[[], bool] | None = None,
        execution_queue: AiExecutionQueue | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._http_client = http_client
        self._ollama_starter = ollama_starter
        self._execution_queue = execution_queue or global_ai_execution_queue()

    def prepare(
        self,
        pdf_path: Path,
        mode: SummaryMode | str = SummaryMode.QUICK,
        *,
        purpose: str = "manual",
    ) -> PreparedSummary:
        settings = settings_for_summary_purpose(
            load_settings(self._settings_path),
            purpose,
        )
        return prepare_summary(pdf_path, settings, mode)

    def prepare_text(
        self,
        source_path: Path,
        page_texts: list[str],
        mode: SummaryMode | str = SummaryMode.QUICK,
        *,
        purpose: str = "manual",
    ) -> PreparedSummary:
        settings = settings_for_summary_purpose(
            load_settings(self._settings_path),
            purpose,
        )
        return prepare_text_summary(
            source_path, page_texts, settings, mode
        )

    def run(
        self,
        prepared: PreparedSummary,
        *,
        allow_cloud_once: bool = False,
        purpose: str = "manual",
    ) -> SummaryExecution:
        settings = settings_for_summary_purpose(
            load_settings(self._settings_path),
            purpose,
        )
        if settings.summary_provider == "ollama":
            starter = self._ollama_starter
            if starter is None:
                from paper_organizer.infra.ollama_installer import start_runtime

                starter = start_runtime
            if not starter():
                raise SummaryPreparationError(
                    "Ollama 서버를 시작할 수 없습니다. "
                    "AI 설정에서 Ollama 설치 상태를 확인하세요."
                )
        with self._execution_queue.slot(
            "analysis",
            prepared.preview.pdf_path.name,
            priority=(
                AI_PRIORITY_MANUAL
                if purpose == "manual"
                else AI_PRIORITY_BACKGROUND
            ),
        ):
            return run_prepared_summary(
                prepared,
                settings,
                self._secret_store,
                allow_cloud_once=allow_cloud_once,
                http_client=self._http_client,
            )


def prepare_summary(
    pdf_path: Path,
    settings: AppSettings,
    mode: SummaryMode | str = SummaryMode.QUICK,
) -> PreparedSummary:
    settings.validate()
    selected_mode = SummaryMode(mode)
    provider = settings.summary_provider
    model = _selected_model(settings)
    if not model:
        raise SummaryPreparationError("요약 AI 모델을 먼저 선택하세요.")
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise SummaryPreparationError("PDF 파일이 없습니다.")
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise SummaryPreparationError("PDF를 열 수 없습니다.") from exc
    try:
        if not document.is_pdf:
            raise SummaryPreparationError("PDF 형식이 아닙니다.")
        if document.needs_pass:
            raise SummaryPreparationError("암호화된 PDF는 먼저 잠금을 해제해야 합니다.")
        page_count = document.page_count
        page_indexes = _selected_page_indexes(page_count, selected_mode)
        chunks = [
            f"[PDF PAGE {index + 1}]\n{document[index].get_text('text')}"
            for index in page_indexes
        ]
    finally:
        document.close()
    if len(_clean_text("\n\n".join(chunks))) < MINIMUM_TEXT_CHARS:
        if page_count < 2:
            raise SummaryPreparationError(
                "2페이지 미만 문서는 OCR 대상에서 제외됩니다."
            )
        if provider == "ollama" and not ollama_model_supports_ocr(model):
            raise SummaryPreparationError(
                "OCR 문서는 8B 이상 Ollama 모델에서만 분석할 수 있습니다. "
                "AI 설정에서 8B 모델을 선택한 뒤 다시 시도하세요."
            )
        try:
            from paper_organizer.application.background_ocr import ocr_page_texts

            recognized = ocr_page_texts(
                path,
                page_indexes=page_indexes,
                background=False,
            )
        except Exception as exc:
            raise SummaryPreparationError(
                f"내장 OCR 실행에 실패했습니다: {' '.join(str(exc).split())}"
            ) from None
        chunks = [
            f"[PDF PAGE {index + 1}]\n{recognized[index]}"
            for index in page_indexes
        ]
    return _prepared_from_chunks(path, page_count, page_indexes, chunks, settings, selected_mode)


def prepare_text_summary(
    source_path: Path,
    page_texts: list[str],
    settings: AppSettings,
    mode: SummaryMode | str = SummaryMode.QUICK,
) -> PreparedSummary:
    """Prepare a summary from previously OCRed PaperPack page text."""

    settings.validate()
    selected_mode = SummaryMode(mode)
    path = Path(source_path).expanduser().resolve()
    page_indexes = _selected_page_indexes(len(page_texts), selected_mode)
    chunks = [
        f"[PDF PAGE {index + 1}]\n{page_texts[index]}" for index in page_indexes
    ]
    return _prepared_from_chunks(
        path, len(page_texts), page_indexes, chunks, settings, selected_mode
    )


def _prepared_from_chunks(
    path: Path,
    page_count: int,
    page_indexes: tuple[int, ...],
    chunks: list[str],
    settings: AppSettings,
    selected_mode: SummaryMode,
) -> PreparedSummary:
    provider = settings.summary_provider
    model = _selected_model(settings)
    if not model:
        raise SummaryPreparationError("요약 AI 모델을 먼저 선택하세요.")
    page_texts = [
        re.sub(r"^\[PDF PAGE \d+\]\s*\n?", "", chunk, count=1)
        for chunk in chunks
    ]
    bundle = detect_document_bundle(page_texts)
    if bundle.is_multiple:
        raise SummaryPreparationError(
            "복수 문서 묶음은 AI 요약하지 않습니다. 문서를 각각 분리한 뒤 다시 분석하세요."
        )
    document_type_decision = classify_document_type(page_texts)
    document_type = document_type_decision.document_type
    is_patent = document_type == PATENT
    if is_patent:
        page_texts = [_remove_patent_page_markers(text) for text in page_texts]
    patent_claims_text = (
        _extract_patent_claims(page_texts) if is_patent else ""
    )
    page_texts = list(remove_figure_and_table_captions(page_texts))
    page_texts = list(remove_publisher_proof_boilerplate(page_texts))
    if is_patent:
        page_texts = list(_remove_patent_drawing_sections(page_texts))
    processed = preprocess_paper_text(
        page_texts,
        page_numbers=tuple(index + 1 for index in page_indexes),
    )
    full_text = processed.text
    if len(full_text) < MINIMUM_TEXT_CHARS:
        raise SummaryPreparationError(
            "내장 OCR을 실행했지만 인식된 본문이 너무 적습니다."
        )
    abstract_only_model = _uses_abstract_only_summary(settings, model)
    abstract_text, abstract_pages = _abstract_source(
        processed,
        page_texts,
        tuple(index + 1 for index in page_indexes),
    )
    bibliography_text = _bibliography_context(page_texts[0])
    if len(page_texts) > 1 and (
        not abstract_pages or page_indexes[0] + 1 not in abstract_pages
    ):
        second_page_bibliography = _bibliography_context(page_texts[1])
        wrapper_pages = {
            page.pdf_page
            for page in detect_wrapper_pages(page_texts)
            if page.confidence >= 0.8
        }
        first_score = _bibliography_identity_score(bibliography_text)
        second_score = _bibliography_identity_score(second_page_bibliography)
        if _has_bibliography_identity_context(second_page_bibliography) and (
            1 in wrapper_pages or second_score >= first_score + 2
        ):
            bibliography_text = second_page_bibliography
    document_type_text = (
        f"[TITLE PAGE]\n{bibliography_text}\n\n[ABSTRACT]\n{abstract_text}"
    )[:16_000]
    summary_strategy = (
        "abstract_only" if abstract_text else "bibliography_only"
    ) if abstract_only_model else (
        "hierarchical" if _uses_hierarchical_summary(settings, model) else "direct"
    )
    analysis_text = (
        f"[SECTION: Abstract | PDF PAGES: {','.join(map(str, abstract_pages))}]\n\n"
        f"[PARAGRAPH 1]\n{abstract_text}"
        if summary_strategy == "abstract_only"
        else ""
        if summary_strategy == "bibliography_only"
        else full_text
    )
    context_window = _adaptive_context_window(settings, model, len(analysis_text))
    max_chars = QUICK_MAX_CHARS if selected_mode is SummaryMode.QUICK else FULL_MAX_CHARS
    if context_window is not None:
        max_chars = min(max_chars, max(4_000, (context_window - CONTEXT_TOKEN_RESERVE) * 4))
    if abstract_only_model:
        text, truncated = _truncate_text(analysis_text, max_chars)
    else:
        text, truncated = _truncate_section_context(processed, max_chars)
    sends_to_cloud = provider in {"openai", "anthropic"}
    preview = SummaryPreview(
        pdf_path=path,
        mode=selected_mode,
        provider=provider,
        model=model,
        page_count=page_count,
        included_pdf_pages=(
            abstract_pages if abstract_only_model else processed.included_pdf_pages
        ),
        character_count=len(text),
        estimated_input_tokens=math.ceil(len(text) / 4),
        truncated=truncated,
        sends_to_cloud=sends_to_cloud,
        requires_cloud_consent=sends_to_cloud and not settings.cloud_processing_consent,
        context_window=context_window,
        included_sections=(
            ("Abstract",) if summary_strategy == "abstract_only" else ()
        ) if abstract_only_model else tuple(
            section.label for section in processed.sections
        ),
        output_language=settings.summary_language,
        summary_strategy=summary_strategy,
        document_type=document_type,
    )
    return PreparedSummary(
        preview=preview,
        document_text=text,
        section_contexts=(
            () if abstract_only_model else _section_contexts(processed)
        ),
        bibliography_text=bibliography_text,
        document_type_text=document_type_text,
        requires_document_type_confirmation=(
            document_type_decision.requires_ai_confirmation
        ),
        regex_fallback=(
            RegexSummaryFallback(
                abstract=abstract_text,
                abstract_pdf_pages=abstract_pages,
            )
            if abstract_only_model
            else _regex_summary_fallback(processed)
        ),
        patent_claims_text=patent_claims_text,
    )


def _regex_summary_fallback(
    processed: PreprocessedDocument,
) -> RegexSummaryFallback:
    abstract = next(
        (section for section in processed.sections if section.name == "abstract"),
        None,
    )
    return RegexSummaryFallback(
        abstract=(
            "\n\n".join(abstract.paragraphs).strip()
            if abstract is not None
            else ""
        ),
        abstract_pdf_pages=(
            abstract.pdf_pages if abstract is not None else ()
        ),
        facts=processed.regex_facts,
    )


def run_prepared_summary(
    prepared: PreparedSummary,
    settings: AppSettings,
    secret_store: SecretStore,
    *,
    allow_cloud_once: bool = False,
    http_client: JsonHttpClient | None = None,
) -> SummaryExecution:
    settings.validate()
    expected = (settings.summary_provider, _selected_model(settings))
    actual = (prepared.preview.provider, prepared.preview.model)
    if actual != expected:
        raise SummaryPreparationError(
            "AI 제공자 또는 모델이 변경되었습니다. 전송 미리보기를 다시 확인하세요."
        )
    provider = build_provider(settings, secret_store, http_client=http_client)
    consent = settings.cloud_processing_consent or allow_cloud_once
    prepared = _confirm_ambiguous_document_type(prepared, provider, consent)
    bibliography_result: BibliographyResult | None = None
    bibliography_retry_count = 0
    bibliography_verified_fields: tuple[str, ...] = ()
    bibliography_status = "ok"
    if _has_bibliography_identity_context(prepared.bibliography_text):
        try:
            (
                bibliography_result,
                bibliography_retry_count,
                bibliography_verified_fields,
            ) = _extract_verified_bibliography(
                provider,
                BibliographyRequest(
                    document_text=prepared.bibliography_text,
                    cloud_consent=consent,
                    context_window=prepared.preview.context_window,
                    is_patent=prepared.preview.document_type == PATENT,
                ),
            )
            if len(bibliography_verified_fields) < 4:
                bibliography_status = "partial"
        except ProviderError:
            bibliography_status = "failed"
    else:
        bibliography_status = "unavailable"
    request_options = {
        "cloud_consent": consent,
        "allowed_categories": _allowed_categories(settings),
        "context_window": prepared.preview.context_window,
        "output_language": settings.summary_language,
        "is_patent": prepared.preview.document_type == PATENT,
        "document_type": prepared.preview.document_type,
    }
    hierarchical = (
        prepared.preview.summary_strategy == "hierarchical"
        and len(prepared.section_contexts) > 1
    )
    abstract_only = prepared.preview.summary_strategy == "abstract_only"
    bibliography_only = prepared.preview.summary_strategy == "bibliography_only"
    request_options["advanced_analysis"] = not (
        hierarchical or abstract_only or bibliography_only
    )
    json_retry_count = 0
    language_retry_count = 0
    if bibliography_only:
        result = SummaryResult(
            provider=prepared.preview.provider,
            model=prepared.preview.model,
            prompt_version="paper-bibliography-only-v1",
            data=SummaryData.from_section_text(""),
        )
    elif hierarchical:
        partial_options = dict(request_options)
        partial_options["allowed_categories"] = ()
        partials: list[SummaryResult] = []
        for context in prepared.section_contexts:
            partial, json_retried, language_retried = (
                _summarize_with_language_retry(
                    provider,
                    SummaryRequest(
                        document_text=context,
                        max_output_tokens=300,
                        prompt_version=(
                            "patent-summary-v1-section"
                            if request_options["is_patent"]
                            else "review-summary-v4-section"
                            if prepared.preview.document_type == "review_paper"
                            else "paper-summary-v10-section"
                        ),
                        stage="section",
                        **partial_options,
                    ),
                    source_text=context,
                )
            )
            partials.append(partial)
            json_retry_count += json_retried
            language_retry_count += language_retried
        synthesis_text = _render_section_summaries(
            partials,
            facts=prepared.regex_fallback.facts,
        )
        result, json_retried, language_retried = _summarize_with_language_retry(
            provider,
            SummaryRequest(
                document_text=synthesis_text,
                prompt_version=(
                    "patent-summary-v1-hierarchical"
                    if request_options["is_patent"]
                    else "review-summary-v4-hierarchical"
                    if prepared.preview.document_type == "review_paper"
                    else "paper-summary-v10-hierarchical"
                ),
                stage="synthesis",
                **request_options,
            ),
            source_text=prepared.document_text,
        )
        json_retry_count += json_retried
        language_retry_count += language_retried
        input_tokens = _sum_optional_tokens(
            [partial.input_tokens for partial in partials]
            + [result.input_tokens]
        )
        output_tokens = _sum_optional_tokens(
            [partial.output_tokens for partial in partials]
            + [result.output_tokens]
        )
        result = replace(
            result,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    else:
        result, json_retried, language_retried = _summarize_with_language_retry(
            provider,
            SummaryRequest(
                document_text=prepared.document_text,
                prompt_version=(
                    "paper-abstract-v1"
                    if abstract_only
                    else "patent-summary-v1-direct"
                    if request_options["is_patent"]
                    else "review-summary-v4-direct"
                    if prepared.preview.document_type == "review_paper"
                    else "paper-summary-v10-direct"
                ),
                stage="abstract" if abstract_only else "direct",
                **request_options,
            ),
            source_text=prepared.document_text,
        )
        json_retry_count += json_retried
        language_retry_count += language_retried
    if bibliography_result is not None:
        bibliography = bibliography_result.data
        result = replace(
            result,
            data=replace(
                result.data,
                title=bibliography.title,
                authors=bibliography.authors,
                year=bibliography.year,
                venue=bibliography.venue,
            ),
            input_tokens=_sum_optional_tokens(
                [bibliography_result.input_tokens, result.input_tokens]
            ),
            output_tokens=_sum_optional_tokens(
                [bibliography_result.output_tokens, result.output_tokens]
            ),
        )
    if hierarchical and (result.data.contributions or result.data.limitations):
        result = replace(
            result,
            data=replace(result.data, contributions=(), limitations=()),
        )
    if abstract_only:
        result = replace(
            result,
            data=replace(
                result.data,
                research_question="",
                methods=(),
                contributions=(),
                limitations=(),
                keywords=(),
                meta_tags=(),
            ),
        )
    if prepared.preview.document_type == "review_paper" and not (
        abstract_only or bibliography_only
    ):
        review_source = "\n\n".join(prepared.section_contexts)
        sanitized_methods = _review_methods_supported_by_source(
            result.data.methods,
            review_source or prepared.document_text,
        )
        sanitized_methods = _ensure_review_nature_method(
            sanitized_methods,
            review_source or prepared.document_text,
            prepared.preview.output_language,
        )
        if sanitized_methods != result.data.methods:
            result = replace(
                result,
                data=replace(result.data, methods=sanitized_methods),
            )
    normalized_summary = _paragraphize_summary(result.data.summary)
    if normalized_summary != result.data.summary:
        result = replace(
            result,
            data=replace(result.data, summary=normalized_summary),
        )
    return SummaryExecution(
        preview=prepared.preview,
        result=result,
        json_retry_count=json_retry_count,
        language_retry_count=language_retry_count,
        bibliography_retry_count=bibliography_retry_count,
        bibliography_status=bibliography_status,
        bibliography_verified_fields=bibliography_verified_fields,
        patent_claims_text=prepared.patent_claims_text,
    )


def _summarize_with_json_retry(
    provider: SummaryProvider,
    request: SummaryRequest,
) -> tuple[SummaryResult, int]:
    try:
        return provider.summarize(request), 0
    except ProviderError as exc:
        if (
            request.json_retry
            or request.json_repair
            or not _is_summary_format_error(exc)
        ):
            raise
    retry_request = replace(
        request,
        prompt_version=f"{request.prompt_version}-json-retry",
        json_retry=True,
    )
    try:
        return provider.summarize(retry_request), 1
    except ProviderError as exc:
        if not _is_summary_format_error(exc):
            raise
    repair_request = replace(
        request,
        prompt_version=f"{request.prompt_version}-json-repair",
        json_repair=True,
    )
    try:
        return provider.summarize(repair_request), 2
    except ProviderError as exc:
        if _is_summary_format_error(exc):
            raise SummaryRetryExhaustedError(
                "AI가 형식 교정 요청을 포함해 세 번 연속 올바른 서지정보 입력을 "
                "만들지 못했습니다. 같은 논문을 다시 시도하거나 더 큰 모델을 "
                "선택하세요.",
                failure_kind="json_validation",
                attempts=3,
            ) from None
        raise


def _extract_verified_bibliography(
    provider: SummaryProvider,
    request: BibliographyRequest,
) -> tuple[BibliographyResult, int, tuple[str, ...]]:
    """Extract a small first-page record and keep only source-verifiable values."""

    try:
        first = provider.extract_bibliography(request)
    except ProviderError:
        retry_request = replace(
            request,
            prompt_version=f"{request.prompt_version}-retry",
            retry=True,
        )
        retry = provider.extract_bibliography(retry_request)
        validated, verified, _needs_retry = _validate_bibliography(
            retry.data,
            request.document_text,
            is_patent=request.is_patent,
        )
        return replace(retry, data=validated), 1, verified

    validated, verified, needs_retry = _validate_bibliography(
        first.data,
        request.document_text,
        is_patent=request.is_patent,
    )
    if not needs_retry:
        return replace(first, data=validated), 0, verified

    retry_request = replace(
        request,
        prompt_version=f"{request.prompt_version}-retry",
        retry=True,
    )
    try:
        retry = provider.extract_bibliography(retry_request)
    except ProviderError:
        return replace(first, data=validated), 1, verified
    retried, retry_verified, _needs_retry = _validate_bibliography(
        retry.data,
        request.document_text,
        is_patent=request.is_patent,
    )
    first_fields = set(verified)
    retry_fields = set(retry_verified)
    merged = BibliographyData(
        title=(
            retried.title if "title" in retry_fields else validated.title
        ),
        authors=(
            retried.authors if "authors" in retry_fields else validated.authors
        ),
        year=retried.year if "year" in retry_fields else validated.year,
        venue=retried.venue if "venue" in retry_fields else validated.venue,
    )
    merged_fields = tuple(
        name
        for name in ("title", "authors", "year", "venue")
        if name in first_fields or name in retry_fields
    )
    return (
        replace(
            retry,
            data=merged,
            input_tokens=_sum_optional_tokens(
                [first.input_tokens, retry.input_tokens]
            ),
            output_tokens=_sum_optional_tokens(
                [first.output_tokens, retry.output_tokens]
            ),
        ),
        1,
        merged_fields,
    )


def _validate_bibliography(
    data: BibliographyData,
    source_text: str,
    *,
    is_patent: bool,
) -> tuple[BibliographyData, tuple[str, ...], bool]:
    """Reject hallucinated or distributor-derived first-page metadata."""

    source = _normalize_bibliography_text(source_text)
    title = _strip_document_type_title_prefix(data.title.strip())
    title_valid = bool(
        title
        and not is_generic_document_heading(title)
        and _normalized_value_present(title, source)
    )

    authors = tuple(
        cleaned
        for author in data.authors
        if author.strip()
        and not _is_distribution_platform(author)
        and _normalized_value_present(author, source)
        and _looks_like_author_name(author)
        if (cleaned := _clean_author_name(author))
    )
    authors_valid = bool(authors)

    year = data.year.strip()
    year_valid = bool(
        re.fullmatch(r"(?:18|19|20|21)\d{2}", year)
        and _publication_year_present(year, source_text)
    )

    venue = "" if is_patent else data.venue.strip()
    venue_valid = bool(
        venue
        and not _is_distribution_platform(venue)
        and _normalized_value_present(venue, source)
    )
    verified = tuple(
        name
        for name, valid in (
            ("title", title_valid),
            ("authors", authors_valid),
            ("year", year_valid),
            ("venue", venue_valid),
        )
        if valid
    )
    invalid_nonempty = (
        (bool(title) and not title_valid)
        or (bool(data.authors) and len(authors) != len(data.authors))
        or (bool(year) and not year_valid)
        or (bool(data.venue.strip()) and not is_patent and not venue_valid)
    )
    needs_retry = invalid_nonempty or not title_valid or not authors_valid
    return (
        BibliographyData(
            title=title if title_valid else "",
            authors=authors,
            year=year if year_valid else "",
            venue=venue if venue_valid else "",
        ),
        verified,
        needs_retry,
    )


def _normalized_value_present(value: str, normalized_source: str) -> bool:
    normalized = _normalize_bibliography_text(value)
    if not normalized:
        return False
    if normalized in normalized_source:
        return True
    # Some publisher PDFs omit spaces inside visually separated title spans
    # (for example, "TheStabilityImprovementofα-Amylase"). Accept only a
    # contiguous match after removing separators; word order and every
    # alphanumeric character must still agree with the page.
    compact_value = re.sub(r"\s+", "", normalized)
    compact_source = re.sub(r"\s+", "", normalized_source)
    return len(compact_value) >= 8 and compact_value in compact_source


def _clean_author_name(value: str) -> str:
    """Remove inline affiliation markers while retaining the personal name."""

    cleaned = " ".join(value.replace("\u00a0", " ").split()).strip(" ,;&")
    cleaned = re.sub(
        r"(?<=[^\W\d_])\s*\d+(?:\s*[,.-]\s*\d+)*\s*$",
        "",
        cleaned,
    )
    return cleaned.strip(" ,;&")


def _looks_like_author_name(value: str) -> bool:
    """Reject body prose that a small model returned as an author entry."""

    cleaned = _clean_author_name(value)
    if not cleaned or len(cleaned) > 100:
        return False
    tokens = re.findall(r"[^\W\d_]+(?:[.'’-][^\W\d_]+)*", cleaned, re.UNICODE)
    if not 1 <= len(tokens) <= 8:
        return False
    prose_words = {
        "the",
        "this",
        "that",
        "study",
        "analysis",
        "measurement",
        "human",
        "its",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "with",
    }
    if len(tokens) >= 3 and any(token.casefold() in prose_words for token in tokens):
        return False
    if any("가" <= character <= "힣" for character in cleaned):
        return len(cleaned) <= 30 and len(tokens) <= 4
    name_like = sum(token[0].isupper() for token in tokens)
    return name_like >= max(1, math.ceil(len(tokens) * 0.6))


def _strip_document_type_title_prefix(value: str) -> str:
    """Remove a neighboring page label accidentally joined to the real title."""

    return re.sub(
        r"^(?:(?:open\s+access\s+)?(?:research|original|review|case|short|brief)\s+"
        r"(?:article|paper)|article)\s*[:.\-–—]?\s+",
        "",
        value,
        flags=re.I,
    ).strip()


def _publication_year_present(year: str, source_text: str) -> bool:
    """Accept publication-zone years while rejecting received and cited years."""

    boundary_match = re.search(
        r"(?im)^\s*(?:abstract|summary|introduction|background|초록|요약|서론|배경)\b",
        source_text,
    )
    front_boundary = boundary_match.start() if boundary_match else len(source_text)
    for match in re.finditer(rf"(?<!\d){re.escape(year)}(?!\d)", source_text):
        before = source_text[max(0, match.start() - 100) : match.start()]
        after = source_text[match.end() : match.end() + 40]
        if re.search(r"\b(?:received|revised|accepted|submitted)\b[^\n]{0,40}$", before, re.I):
            continue
        if re.search(r"(?:et\s+al\.|[A-Z][A-Za-z'’-]+)\s*,?\s*\(?$", before, re.I) and re.match(
            r"\)?(?:[,.;]|\s)", after
        ):
            continue
        context = before + year + after
        if re.search(
            r"\b(?:journal|proceedings|transactions|letters|vol(?:ume)?\.?|"
            r"published|publication|copyright)\b|©|\b\d+\s*\("
            + re.escape(year)
            + r"\)\s*\d+",
            context,
            re.I,
        ):
            return True
        if match.start() < front_boundary and not re.match(r"\s*\)", after):
            return True
    return False


def _confirm_ambiguous_document_type(
    prepared: PreparedSummary,
    provider: SummaryProvider,
    cloud_consent: bool,
) -> PreparedSummary:
    """Use AI only to verify weak sentence-level review markers."""

    if not prepared.requires_document_type_confirmation:
        return prepared
    document_type = RESEARCH_PAPER
    source = "auto:regex"
    try:
        result = provider.classify_document_type(
            DocumentTypeRequest(
                document_text=prepared.document_type_text,
                cloud_consent=cloud_consent,
                context_window=prepared.preview.context_window,
            )
        )
    except ProviderError:
        pass
    else:
        if result.data.document_type in {RESEARCH_PAPER, REVIEW_PAPER}:
            document_type = result.data.document_type
            source = f"ai:{result.provider}"
    return replace(
        prepared,
        preview=replace(
            prepared.preview,
            document_type=document_type,
            document_type_source=source,
        ),
        requires_document_type_confirmation=False,
    )


def _abstract_source(
    processed: PreprocessedDocument,
    page_texts: list[str],
    page_numbers: tuple[int, ...],
) -> tuple[str, tuple[int, ...]]:
    """Return only an explicit or title-page abstract, never body substitution."""

    abstract = next(
        (section for section in processed.sections if section.name == "abstract"),
        None,
    )
    if abstract is not None:
        value = "\n\n".join(abstract.paragraphs).strip()
        if _looks_like_abstract(value):
            return value, abstract.pdf_pages
    for page_index, page in enumerate(page_texts[:2]):
        explicit = re.search(
            r"(?ims)(?:^|\n)\s*(?:abstract|a\s+b\s+s\s+t\s+r\s+a\s+c\s+t|초록)"
            r"\s*[:.]?\s*(.*?)"
            r"(?=\n\s*(?:keywords?|key\s*words|index\s+terms|"
            r"\d+(?:\.\d+)*[.)]?\s*(?:introduction|서론)|introduction|서론)\b)",
            page,
        )
        if explicit:
            value = " ".join(explicit.group(1).split()).strip()
            if _looks_like_abstract(value):
                return value[:6_000], (page_numbers[page_index],)
        keyword = re.search(r"(?im)^\s*(?:keywords?|key\s*words)\s*[:.]", page)
        if not keyword:
            continue
        prefix = page[: keyword.start()]
        lines = prefix.splitlines()
        affiliation_indexes = [
            index
            for index, line in enumerate(lines)
            if re.search(
                r"\b(?:department|university|institute|school|faculty|college|"
                r"hospital|laboratory|centre|center)\b",
                line,
                re.I,
            )
        ]
        if not affiliation_indexes:
            continue
        value = " ".join(lines[affiliation_indexes[-1] + 1 :]).strip()
        value = re.sub(r"\s+", " ", value)
        if 120 <= len(value) <= 6_000:
            return value, (page_numbers[page_index],)
    return "", ()


def _looks_like_abstract(value: str) -> bool:
    normalized = " ".join(value.split()).strip()
    if len(normalized) < 80:
        return False
    boilerplate_hits = sum(
        marker in normalized.casefold()
        for marker in (
            "article history",
            "received in revised form",
            "available online",
            "handling editor",
        )
    )
    return boilerplate_hits < 2


def _normalize_bibliography_text(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value.casefold(), flags=re.UNICODE).strip()


def _is_distribution_platform(value: str) -> bool:
    normalized = _normalize_bibliography_text(value)
    return any(
        _normalize_bibliography_text(name) in normalized
        for name in _DISTRIBUTION_PLATFORM_NAMES
    )


def _summarize_with_language_retry(
    provider: SummaryProvider,
    request: SummaryRequest,
    *,
    source_text: str,
) -> tuple[SummaryResult, int, int]:
    """Retry once when explanatory fields violate the selected language."""

    result, json_retries = _summarize_with_json_retry(provider, request)
    if not _summary_language_mismatch(
        result,
        source_text=source_text,
        output_language=request.output_language,
    ):
        return result, json_retries, 0
    retry_request = replace(
        request,
        prompt_version=f"{request.prompt_version}-language-retry",
        language_retry=True,
    )
    retry, retry_json_retries = _summarize_with_json_retry(
        provider,
        retry_request,
    )
    if _summary_language_mismatch(
        retry,
        source_text=source_text,
        output_language=request.output_language,
    ):
        requested = (
            "한국어"
            if request.output_language == "ko"
            else "논문 원문 언어"
        )
        raise SummaryRetryExhaustedError(
            f"AI가 두 번 연속 {requested} 요약 지시를 따르지 못했습니다. "
            "같은 논문을 다시 시도하거나 다른 모델을 선택하세요.",
            failure_kind="language_validation",
            attempts=2,
        )
    retry = replace(
        retry,
        input_tokens=_sum_optional_tokens(
            [result.input_tokens, retry.input_tokens]
        ),
        output_tokens=_sum_optional_tokens(
            [result.output_tokens, retry.output_tokens]
        ),
    )
    return retry, json_retries + retry_json_retries, 1


def _summary_language_mismatch(
    result: SummaryResult,
    *,
    source_text: str,
    output_language: str,
) -> bool:
    """Detect clear English/Korean contract violations without inspecting categories."""

    data = result.data
    explanatory_text = " ".join(
        (
            data.summary,
            data.research_question,
            *data.methods,
            *data.contributions,
            *data.limitations,
            *data.keywords,
            *data.meta_tags,
        )
    )
    if not explanatory_text.strip():
        return False
    source_latin = len(re.findall(r"[A-Za-z]", source_text))
    source_hangul = len(re.findall(r"[가-힣]", source_text))
    source_han_kana = len(
        re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff]", source_text)
    )
    output_latin = len(re.findall(r"[A-Za-z]", explanatory_text))
    output_hangul = len(re.findall(r"[가-힣]", explanatory_text))
    output_han_kana = len(
        re.findall(
            r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff]",
            explanatory_text,
        )
    )
    english_source = (
        source_latin >= 100
        and source_latin >= max(1, source_hangul + source_han_kana) * 3
    )
    korean_source = (
        source_hangul >= 100
        and source_hangul >= max(1, source_latin)
    )
    if output_language == "source" and english_source:
        return output_hangul + output_han_kana > 0
    expects_korean = output_language == "ko" or (
        output_language == "source" and korean_source
    )
    if not expects_korean:
        return False
    letters = output_hangul + output_latin
    return output_hangul < 10 or (
        letters >= 40 and output_hangul / letters < 0.08
    )


_FORMAL_REVIEW_METHOD_MARKERS = (
    "systematic review",
    "meta-analysis",
    "meta analysis",
    "prisma",
    "eligibility criteria",
    "literature screening",
    "database search",
    "pubmed",
    "scopus",
    "web of science",
    "cochrane",
)


def _review_methods_supported_by_source(
    methods: tuple[str, ...],
    source_text: str,
) -> tuple[str, ...]:
    """Drop formal review methods that have no literal support in the paper."""

    source = source_text.casefold()
    return tuple(
        method
        for method in methods
        if not any(
            marker in method.casefold() and marker not in source
            for marker in _FORMAL_REVIEW_METHOD_MARKERS
        )
    )


def _ensure_review_nature_method(
    methods: tuple[str, ...],
    source_text: str,
    output_language: str,
) -> tuple[str, ...]:
    """Make the already-classified review nature explicit for downstream QA."""

    joined = " ".join(methods).casefold()
    if any(
        marker in joined
        for marker in (
            "no new controlled experiment",
            "no original controlled experiment",
            "새로운 대조 실험",
            "신규 대조 실험",
        )
    ):
        return methods
    hangul = len(re.findall(r"[가-힣]", source_text))
    latin = len(re.findall(r"[A-Za-z]", source_text))
    if output_language == "ko" or (hangul >= 100 and hangul >= latin):
        marker = "문헌을 종합한 리뷰논문이며 새로운 대조 실험을 수행하지 않음"
    elif latin >= 100 and latin >= max(1, hangul) * 3:
        marker = "Literature review and synthesis; no new controlled experiment was performed."
    else:
        return methods
    return (marker, *methods)


def _is_summary_format_error(error: ProviderError) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "invalid json",
            "summary must be a json object",
            "invalid summary fields",
            "summary field",
        )
    )


def _allowed_categories(settings: AppSettings) -> tuple[str, ...]:
    """Limit AI classification to the bundled taxonomy, narrowed by preference."""

    try:
        bundled_names = taxonomy_category_names()
    except TaxonomyError:
        bundled_names = ()
    names = tuple(settings.research_categories) or bundled_names
    focus = [name.strip() for name in settings.focus_categories if name.strip()]
    if focus:
        chosen = {name for name in focus if name in names}
        return tuple(name for name in names if name in chosen)
    return tuple(names)


def _selected_page_indexes(page_count: int, mode: SummaryMode) -> tuple[int, ...]:
    if mode is SummaryMode.FULL:
        return tuple(range(page_count))
    if page_count <= 18:
        return tuple(range(page_count))
    front = list(range(6))
    tail = list(range(page_count - 3, page_count))
    interior_start = 6
    interior_end = page_count - 4
    span = interior_end - interior_start
    sampled = [
        round(interior_start + span * index / 8)
        for index in range(9)
    ]
    return tuple(dict.fromkeys(front + sampled + tail))


def _selected_model(settings: AppSettings) -> str:
    if settings.summary_provider == "ollama":
        return settings.selected_model.strip()
    if settings.summary_provider == "openai":
        return settings.openai_model.strip()
    return settings.anthropic_model.strip()


def _model_parameters(model: str) -> float:
    try:
        from paper_organizer.core.model_recommendation import load_model_catalog

        key = model.strip().casefold().removesuffix(":latest")
        for spec in load_model_catalog()[1]:
            if spec.model_id.casefold().removesuffix(":latest") == key:
                return spec.parameters_b
    except (OSError, ValueError, KeyError):
        pass
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?:\b|$)", model.casefold())
    return float(match.group(1)) if match else 0.0


def ollama_model_supports_ocr(model: str) -> bool:
    """Return whether a known Ollama model meets the OCR analysis floor."""

    return _model_parameters(model) >= OCR_MINIMUM_OLLAMA_PARAMETERS_B


def _uses_hierarchical_summary(settings: AppSettings, model: str) -> bool:
    parameters = _model_parameters(model)
    return settings.summary_provider == "ollama" and 0 < parameters < 8.0


def _uses_abstract_only_summary(settings: AppSettings, model: str) -> bool:
    return (
        settings.summary_provider == "ollama"
        and model.strip().casefold().removesuffix(":latest") == "qwen3:1.7b"
    )


def _adaptive_context_window(
    settings: AppSettings, model: str, character_count: int
) -> int | None:
    """Choose a quiet local context that fits the model, PC and paper length."""

    if settings.summary_provider != "ollama":
        return None
    parameters = _model_parameters(model)
    hardware = settings.hardware_profile
    try:
        memory_gb = float(hardware.get("memory_total_gb") or 0)
    except (TypeError, ValueError):
        memory_gb = 0.0
    try:
        available_memory_gb = float(hardware.get("memory_available_gb") or 0)
    except (TypeError, ValueError):
        available_memory_gb = 0.0
    gpu_vram = 0.0
    gpus = hardware.get("gpus", [])
    for gpu in gpus if isinstance(gpus, list) else []:
        if not isinstance(gpu, dict):
            continue
        try:
            gpu_vram = max(
                gpu_vram,
                float(
                    gpu.get("dedicated_memory_gb")
                    or gpu.get("vram_total_gb")
                    or gpu.get("vram_gb")
                    or gpu.get("memory_total_gb")
                    or 0
                ),
            )
        except (TypeError, ValueError):
            continue

    if 3.0 <= parameters < 6.0:
        if gpu_vram >= 6:
            maximum = 24_576
        elif (
            settings.resource_profile != "eco"
            and memory_gb >= 24
            and available_memory_gb >= 10
        ):
            maximum = 24_576
        elif (
            settings.resource_profile != "eco"
            and memory_gb >= 16
            and available_memory_gb >= 7
        ):
            maximum = 16_384
        else:
            # Intel/AMD iGPUs share system RAM. A 16K context made a 3B model
            # consume about 3.6 GB on a 16 GB PC, leaving too little for the UI.
            maximum = 8_192
    elif 6.0 <= parameters <= 10.0:
        maximum = 16_384
        if memory_gb >= 16 or gpu_vram >= 8:
            maximum = 24_576
        if (
            settings.resource_profile == "performance"
            and (memory_gb >= 24 or gpu_vram >= 12)
        ):
            maximum = 40_960
    else:
        return None

    needed = math.ceil(character_count / 4) + CONTEXT_TOKEN_RESERVE
    for bucket in (8_192, 16_384, 24_576, 40_960):
        if needed <= bucket:
            return min(bucket, maximum)
    return maximum


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _bibliography_context(first_page_text: str) -> str:
    """Keep first-page identity text while removing download-platform furniture."""

    kept: list[str] = []
    for raw_line in first_page_text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        lowered = line.casefold()
        if any(name in lowered for name in _DISTRIBUTION_PLATFORM_NAMES):
            continue
        if (
            ("download" in lowered or "uploaded" in lowered)
            and ("http://" in lowered or "https://" in lowered or "www." in lowered)
        ):
            continue
        kept.append(line)
    return "\n".join(kept)[:_BIBLIOGRAPHY_MAX_CHARS].strip()


_PATENT_CLAIMS_START_RE = re.compile(
    r"(?im)^[ \t]*(?:"
    r"claims?[ \t]*:?"
    r"|what[ \t]+is[ \t]+claimed[ \t]+is[ \t]*:?"
    r"|청구[ \t]*범위[ \t]*:?"
    r"|청구항(?:[ \t]*제?[ \t]*1[ \t]*항?)?[ \t]*:?"
    r")[ \t]*$"
)
_PATENT_CLAIMS_END_RE = re.compile(
    r"(?im)^[ \t]*(?:"
    r"abstract(?:[ \t]+of[ \t]+the[ \t]+disclosure)?"
    r"|description"
    r"|brief[ \t]+description[ \t]+of[ \t]+the[ \t]+drawings"
    r"|drawings?"
    r"|발명의[ \t]+설명"
    r"|도면의[ \t]+간단한[ \t]+설명"
    r"|요약서"
    r")[ \t]*:?[ \t]*$"
)
_PATENT_PAGE_MARKER_RE = re.compile(
    r"^[ \t]*(?:"
    r"\[?[ \t]*(?:pdf[ \t]+)?page[ \t]*[:#.]?[ \t]*\d+"
    r"(?:[ \t]*(?:of|/)[ \t]*\d+)?[ \t]*\]?"
    r"|(?:페이지|쪽)[ \t]*[:#.]?[ \t]*\d+"
    r"(?:[ \t]*(?:중|/)[ \t]*\d+)?"
    r"|\d+[ \t]*(?:of|/)[ \t]*\d+"
    r"|[-–—][ \t]*\d+[ \t]*[-–—]"
    r")[ \t]*$",
    re.IGNORECASE,
)
_PATENT_DRAWING_START_RE = re.compile(
    r"^[ \t]*(?:"
    r"brief[ \t]+description[ \t]+of[ \t]+the[ \t]+drawings"
    r"|drawings?"
    r"|도면의[ \t]+간단한[ \t]+설명"
    r"|도면"
    r")[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)
_PATENT_DRAWING_END_RE = re.compile(
    r"^[ \t]*(?:"
    r"detailed[ \t]+description(?:[ \t]+of[ \t]+the[ \t]+invention)?"
    r"|description[ \t]+of[ \t]+embodiments?"
    r"|best[ \t]+mode"
    r"|claims?"
    r"|what[ \t]+is[ \t]+claimed[ \t]+is"
    r"|abstract(?:[ \t]+of[ \t]+the[ \t]+disclosure)?"
    r"|발명을[ \t]+실시하기[ \t]+위한[ \t]+구체적인[ \t]+내용"
    r"|발명의[ \t]+상세한[ \t]+설명"
    r"|청구[ \t]*범위"
    r"|청구항(?:[ \t]*제?[ \t]*1[ \t]*항?)?"
    r"|요약서"
    r")[ \t]*:?[ \t]*$",
    re.IGNORECASE,
)


def _remove_patent_page_markers(text: str) -> str:
    """Remove standalone printed page labels without touching claim numbers."""

    return "\n".join(
        line
        for line in str(text or "").splitlines()
        if not _PATENT_PAGE_MARKER_RE.fullmatch(line)
    )


def _remove_patent_drawing_sections(
    page_texts: list[str],
) -> tuple[str, ...]:
    """Remove patent drawing-description blocks from temporary AI input."""

    skipping = False
    cleaned: list[str] = []
    for text in page_texts:
        kept: list[str] = []
        for line in str(text or "").splitlines():
            if _PATENT_DRAWING_START_RE.fullmatch(line):
                skipping = True
                continue
            if skipping and _PATENT_DRAWING_END_RE.fullmatch(line):
                skipping = False
            if not skipping:
                kept.append(line)
        cleaned.append("\n".join(kept))
    return tuple(cleaned)


def _extract_patent_claims(page_texts: list[str]) -> str:
    """Copy claims from extracted text, omitting standalone page labels."""

    source = "\n".join(_remove_patent_page_markers(text) for text in page_texts)
    start = _PATENT_CLAIMS_START_RE.search(source)
    if start is None:
        return ""
    end = _PATENT_CLAIMS_END_RE.search(source, start.end())
    return source[start.start() : end.start() if end else len(source)].strip()


def _has_bibliography_identity_context(first_page_text: str) -> bool:
    """Avoid an extra model call when the supplied page is clearly body text only."""

    lines = [line.strip() for line in first_page_text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    front_lines: list[str] = []
    for line in lines:
        if re.fullmatch(
            r"(?:abstract|summary|introduction|background|초록|요약|서론|배경)\s*[:.]?",
            line,
            re.IGNORECASE,
        ):
            break
        front_lines.append(line)
    if len(front_lines) < 2:
        return False
    front = "\n".join(front_lines[:20])
    signals = (
        re.search(r"(?<!\d)(?:18|19|20|21)\d{2}(?!\d)", front),
        re.search(r"\b10\.\d{4,9}/\S+", front, re.IGNORECASE),
        re.search(r"[,;]|\band\b", front, re.IGNORECASE),
        re.search(
            r"\b(?:journal|conference|proceedings|university|institute|department|"
            r"laboratory|centre|center)\b|저널|학회|대학교|연구소|특허",
            front,
            re.IGNORECASE,
        ),
    )
    return any(signals)


def _bibliography_identity_score(value: str) -> int:
    """Score front-matter evidence before replacing the first-page context."""

    score = 0
    if re.search(r"\b10\.\d{4,9}/\S+", value, re.IGNORECASE):
        score += 3
    if re.search(r"(?<!\d)(?:18|19|20|21)\d{2}(?!\d)", value):
        score += 1
    if re.search(r"[,;]|\band\b|\s&\s", value, re.IGNORECASE):
        score += 1
    if re.search(
        r"\b(?:journal|conference|proceedings|university|institute|department|"
        r"laboratory|centre|center)\b|저널|학회|대학교|연구소",
        value,
        re.IGNORECASE,
    ):
        score += 1
    if re.search(
        r"(?im)^\s*(?:abstract|a\s+b\s+s\s+t\s+r\s+a\s+c\s+t|초록|요약)\b",
        value,
    ):
        score += 2
    return score


def _looks_like_patent(first_page_text: str) -> bool:
    normalized = first_page_text.casefold()
    markers = (
        "patent application publication",
        "international publication number",
        "world intellectual property organization",
        "발명의 명칭",
        "공개특허",
        "등록특허",
        "특허출원",
        "대한민국특허청",
    )
    return any(marker in normalized for marker in markers)


def _strip_reference_section(text: str) -> str:
    """Exclude end references from AI input while PaperPack text stays intact."""

    matches = list(_REFERENCE_HEADING_RE.finditer(text))
    if not matches:
        return text
    minimum_offset = max(MINIMUM_TEXT_CHARS, int(len(text) * 0.35))
    candidates = [match for match in matches if match.start() >= minimum_offset]
    if not candidates:
        return text
    cut_at = candidates[-1].start()
    page_marker = text.rfind("[PDF PAGE ", 0, cut_at)
    if page_marker >= 0 and re.fullmatch(
        r"\[PDF PAGE \d+\]\s*",
        text[page_marker:cut_at],
    ):
        cut_at = page_marker
    return text[:cut_at].rstrip()


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    front = int(max_chars * 0.72)
    marker = "\n\n[...context omitted...]\n\n"
    back = max_chars - front - len(marker)
    return text[:front] + marker + text[-back:], True


def _truncate_section_context(
    processed: PreprocessedDocument, max_chars: int
) -> tuple[str, bool]:
    """Keep evidence from every detected section when context must be shortened."""

    if len(processed.text) <= max_chars:
        return processed.text, False
    prefix = ""
    if processed.regex_facts:
        prefix = (
            "[REGEX-VALIDATED CANDIDATES]\n"
            + "\n".join(processed.regex_facts)
            + "\n\n"
        )
    section_count = max(1, len(processed.sections))
    header_chars = sum(
        len(
            f"[SECTION: {section.label} | PDF PAGES: "
            f"{','.join(str(page) for page in section.pdf_pages)}]\n\n"
        )
        for section in processed.sections
    )
    available = max(0, max_chars - len(prefix) - header_chars)
    per_section = max(256, available // section_count)
    blocks: list[str] = []
    for section in processed.sections:
        pages = ",".join(str(page) for page in section.pdf_pages)
        header = f"[SECTION: {section.label} | PDF PAGES: {pages}]"
        used = 0
        paragraphs: list[str] = []
        for index, paragraph in enumerate(section.paragraphs, 1):
            block = f"[PARAGRAPH {index}]\n{paragraph}"
            if paragraphs and used + len(block) + 2 > per_section:
                break
            if not paragraphs and len(block) > per_section:
                block = block[:per_section].rstrip() + "\n[...section context omitted...]"
            paragraphs.append(block)
            used += len(block) + 2
        blocks.append(header + "\n\n" + "\n\n".join(paragraphs))
    text = prefix + "\n\n".join(blocks)
    return text[:max_chars].rstrip(), True


def _paragraphize_summary(value: str) -> str:
    """Normalize model prose into UI-friendly paragraphs after JSON validation."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    existing = [
        " ".join(paragraph.split())
        for paragraph in re.split(r"\n\s*\n", normalized)
        if paragraph.strip()
    ]
    if len(existing) > 1:
        return "\n\n".join(existing)
    lines = [" ".join(line.split()) for line in normalized.splitlines() if line.strip()]
    if len(lines) > 1:
        return "\n\n".join(lines)
    sentences = [
        sentence.strip()
        for sentence in re.split(
            r"(?<=[.!?])\s+(?=[A-Z0-9가-힣])",
            normalized,
        )
        if sentence.strip()
    ]
    if len(sentences) < 4:
        return normalized
    paragraph_count = min(4, max(2, math.ceil(len(sentences) / 2)))
    paragraphs: list[str] = []
    start = 0
    for remaining_groups in range(paragraph_count, 0, -1):
        remaining_sentences = len(sentences) - start
        take = math.ceil(remaining_sentences / remaining_groups)
        paragraphs.append(" ".join(sentences[start : start + take]))
        start += take
    return "\n\n".join(paragraphs)


def _section_contexts(processed: PreprocessedDocument) -> tuple[str, ...]:
    contexts: list[str] = []
    for section in processed.sections:
        pages = ",".join(str(page) for page in section.pdf_pages)
        body = "\n\n".join(
            f"[PARAGRAPH {index}]\n{paragraph}"
            for index, paragraph in enumerate(section.paragraphs, 1)
        )
        context = (
            f"[SECTION: {section.label} | PDF PAGES: {pages}]\n\n"
            + body
        )
        contexts.append(_truncate_text(context, 24_000)[0])
    return tuple(contexts)


def _render_section_summaries(
    results: list[SummaryResult], *, facts: tuple[str, ...] = ()
) -> str:
    blocks: list[str] = []
    if facts:
        blocks.append("[REGEX-VALIDATED CANDIDATES]\n" + "\n".join(facts))
    for index, result in enumerate(results, 1):
        blocks.append(
            f"[SECTION EVIDENCE {index}]\n"
            + json.dumps(
                {
                    "summary": result.data.summary,
                    "research_question": result.data.research_question,
                    "methods": list(result.data.methods),
                    "keywords": list(result.data.keywords),
                    "meta_tags": list(result.data.meta_tags),
                },
                ensure_ascii=False,
            )
        )
    return "\n\n".join(blocks)


def _sum_optional_tokens(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None
