"""Prepare PDF text safely and run an immediate summary without moving files."""

from __future__ import annotations

import math
import json
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable

import fitz

from paper_organizer.core.classifier import TaxonomyError, taxonomy_category_names
from paper_organizer.application.summary_preprocessing import (
    PreprocessedDocument,
    preprocess_paper_text,
)
from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import AppSettings
from paper_organizer.infra.settings import default_settings_path, load_settings
from paper_organizer.providers.base import JsonHttpClient, SummaryRequest, SummaryResult
from paper_organizer.providers.registry import build_provider


QUICK_MAX_CHARS = 30_000
FULL_MAX_CHARS = 120_000
MINIMUM_TEXT_CHARS = 500
CONTEXT_TOKEN_RESERVE = 3_000
_REFERENCE_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:(?:\d+(?:\.\d+)*)[.)]?[ \t]+)?"
    r"(?:references?(?:[ \t]+(?:and[ \t]+notes|and[ \t]+further[ \t]+reading|cited))?|bibliography|"
    r"works[ \t]+cited|literature[ \t]+cited|참고문헌)"
    r"[ \t]*$"
)


class SummaryPreparationError(RuntimeError):
    pass


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


@dataclass(frozen=True, slots=True)
class PreparedSummary:
    preview: SummaryPreview
    document_text: str = field(repr=False)
    section_contexts: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class SummaryExecution:
    preview: SummaryPreview
    result: SummaryResult

    @property
    def provenance(self) -> dict[str, object]:
        return {
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
        }


class ImmediateSummaryController:
    """State boundary used by the widget; it never exposes the secret store."""

    def __init__(
        self,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        http_client: JsonHttpClient | None = None,
        ollama_starter: Callable[[], bool] | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._http_client = http_client
        self._ollama_starter = ollama_starter

    def prepare(
        self, pdf_path: Path, mode: SummaryMode | str = SummaryMode.QUICK
    ) -> PreparedSummary:
        return prepare_summary(pdf_path, load_settings(self._settings_path), mode)

    def prepare_text(
        self,
        source_path: Path,
        page_texts: list[str],
        mode: SummaryMode | str = SummaryMode.QUICK,
    ) -> PreparedSummary:
        return prepare_text_summary(
            source_path, page_texts, load_settings(self._settings_path), mode
        )

    def run(
        self, prepared: PreparedSummary, *, allow_cloud_once: bool = False
    ) -> SummaryExecution:
        settings = load_settings(self._settings_path)
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
    processed = preprocess_paper_text(
        page_texts,
        page_numbers=tuple(index + 1 for index in page_indexes),
    )
    full_text = processed.text
    if len(full_text) < MINIMUM_TEXT_CHARS:
        raise SummaryPreparationError(
            "내장 OCR을 실행했지만 인식된 본문이 너무 적습니다."
        )
    context_window = _adaptive_context_window(settings, model, len(full_text))
    max_chars = QUICK_MAX_CHARS if selected_mode is SummaryMode.QUICK else FULL_MAX_CHARS
    if context_window is not None:
        max_chars = min(max_chars, max(4_000, (context_window - CONTEXT_TOKEN_RESERVE) * 4))
    text, truncated = _truncate_section_context(processed, max_chars)
    sends_to_cloud = provider in {"openai", "anthropic"}
    preview = SummaryPreview(
        pdf_path=path,
        mode=selected_mode,
        provider=provider,
        model=model,
        page_count=page_count,
        included_pdf_pages=processed.included_pdf_pages,
        character_count=len(text),
        estimated_input_tokens=math.ceil(len(text) / 4),
        truncated=truncated,
        sends_to_cloud=sends_to_cloud,
        requires_cloud_consent=sends_to_cloud and not settings.cloud_processing_consent,
        context_window=context_window,
        included_sections=tuple(section.label for section in processed.sections),
        output_language=settings.summary_language,
        summary_strategy=(
            "hierarchical"
            if _uses_hierarchical_summary(settings, model)
            else "direct"
        ),
    )
    return PreparedSummary(
        preview=preview,
        document_text=text,
        section_contexts=_section_contexts(processed),
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
    request_options = {
        "cloud_consent": consent,
        "allowed_categories": _allowed_categories(settings),
        "context_window": prepared.preview.context_window,
        "output_language": settings.summary_language,
    }
    hierarchical = (
        prepared.preview.summary_strategy == "hierarchical"
        and len(prepared.section_contexts) > 1
    )
    request_options["advanced_analysis"] = not hierarchical
    if hierarchical:
        partial_options = dict(request_options)
        partial_options["allowed_categories"] = ()
        partials = [
            provider.summarize(
                SummaryRequest(
                    document_text=context,
                    max_output_tokens=900,
                    prompt_version="paper-summary-v8-section",
                    stage="section",
                    **partial_options,
                )
            )
            for context in prepared.section_contexts
        ]
        synthesis_text = _render_section_summaries(partials)
        final_input = synthesis_text
        result = provider.summarize(
            SummaryRequest(
                document_text=synthesis_text,
                prompt_version="paper-summary-v8-hierarchical",
                stage="synthesis",
                **request_options,
            )
        )
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
        final_input = prepared.document_text
        result = provider.summarize(
            SummaryRequest(
                document_text=prepared.document_text,
                prompt_version="paper-summary-v8-direct",
                stage="direct",
                **request_options,
            )
        )
    if _title_needs_original_language_retry(
        result.data.title, prepared.document_text
    ):
        retry = provider.summarize(
            SummaryRequest(
                document_text=final_input,
                prompt_version="paper-summary-v8-title-retry",
                stage="synthesis" if hierarchical else "direct",
                title_retry=True,
                **request_options,
            )
        )
        result = replace(
            retry,
            input_tokens=_sum_optional_tokens(
                [result.input_tokens, retry.input_tokens]
            ),
            output_tokens=_sum_optional_tokens(
                [result.output_tokens, retry.output_tokens]
            ),
        )
    if hierarchical and (result.data.contributions or result.data.limitations):
        result = replace(
            result,
            data=replace(result.data, contributions=(), limitations=()),
        )
    normalized_summary = _paragraphize_summary(result.data.summary_ko)
    if normalized_summary != result.data.summary_ko:
        result = replace(
            result,
            data=replace(result.data, summary_ko=normalized_summary),
        )
    return SummaryExecution(preview=prepared.preview, result=result)


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
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?:\b|$)", model.casefold())
    return float(match.group(1)) if match else 0.0


def _uses_hierarchical_summary(settings: AppSettings, model: str) -> bool:
    parameters = _model_parameters(model)
    return settings.summary_provider == "ollama" and 0 < parameters <= 4.0


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
        maximum = 24_576 if memory_gb >= 12 or gpu_vram >= 6 else 16_384
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
    for bucket in (16_384, 24_576, 40_960):
        if needed <= bucket:
            return min(bucket, maximum)
    return maximum


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
    facts = ""
    if processed.regex_facts:
        facts = (
            "[REGEX-VALIDATED CANDIDATES]\n"
            + "\n".join(processed.regex_facts)
            + "\n\n"
        )
    contexts: list[str] = []
    for section in processed.sections:
        pages = ",".join(str(page) for page in section.pdf_pages)
        body = "\n\n".join(
            f"[PARAGRAPH {index}]\n{paragraph}"
            for index, paragraph in enumerate(section.paragraphs, 1)
        )
        prefix = facts if section.name == "front" else ""
        context = (
            prefix
            + f"[SECTION: {section.label} | PDF PAGES: {pages}]\n\n"
            + body
        )
        contexts.append(_truncate_text(context, 24_000)[0])
    return tuple(contexts)


def _render_section_summaries(results: list[SummaryResult]) -> str:
    blocks: list[str] = []
    for index, result in enumerate(results, 1):
        blocks.append(
            f"[SECTION EVIDENCE {index}]\n"
            + json.dumps(
                {
                    "summary": result.data.summary_ko,
                    "research_question": result.data.research_question,
                    "methods": list(result.data.methods),
                    "keywords": list(result.data.keywords),
                    "title": result.data.title,
                    "authors": list(result.data.authors),
                    "year": result.data.year,
                    "venue": result.data.venue,
                    "meta_tags": list(result.data.meta_tags),
                },
                ensure_ascii=False,
            )
        )
    return "\n\n".join(blocks)


def _sum_optional_tokens(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _title_needs_original_language_retry(title: str, source_text: str) -> bool:
    """Retry only a clear script mismatch; do not second-guess bilingual papers."""

    normalized_title = title.strip()
    if not normalized_title or not re.search(r"[가-힣]", normalized_title):
        return False
    source_hangul = len(re.findall(r"[가-힣]", source_text))
    source_latin = len(re.findall(r"[A-Za-z]", source_text))
    return source_latin >= 100 and source_hangul == 0
