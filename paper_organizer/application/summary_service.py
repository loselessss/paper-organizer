"""Prepare PDF text safely and run an immediate summary without moving files."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import fitz

from paper_organizer.core.classifier import TaxonomyError, taxonomy_category_names
from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import AppSettings
from paper_organizer.infra.settings import default_settings_path, load_settings
from paper_organizer.providers.base import JsonHttpClient, SummaryRequest, SummaryResult
from paper_organizer.providers.registry import build_provider


QUICK_MAX_CHARS = 30_000
FULL_MAX_CHARS = 120_000
MINIMUM_TEXT_CHARS = 500


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


@dataclass(frozen=True, slots=True)
class PreparedSummary:
    preview: SummaryPreview
    document_text: str = field(repr=False)


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
        }


class ImmediateSummaryController:
    """State boundary used by the widget; it never exposes the secret store."""

    def __init__(
        self,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        http_client: JsonHttpClient | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._http_client = http_client

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
        return run_prepared_summary(
            prepared,
            load_settings(self._settings_path),
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
    text = _clean_text("\n\n".join(chunks))
    if len(text) < MINIMUM_TEXT_CHARS:
        raise SummaryPreparationError(
            "내장 OCR을 실행했지만 인식된 본문이 너무 적습니다."
        )
    max_chars = QUICK_MAX_CHARS if selected_mode is SummaryMode.QUICK else FULL_MAX_CHARS
    text, truncated = _truncate_text(text, max_chars)
    sends_to_cloud = provider in {"openai", "anthropic"}
    preview = SummaryPreview(
        pdf_path=path,
        mode=selected_mode,
        provider=provider,
        model=model,
        page_count=page_count,
        included_pdf_pages=tuple(index + 1 for index in page_indexes),
        character_count=len(text),
        estimated_input_tokens=math.ceil(len(text) / 4),
        truncated=truncated,
        sends_to_cloud=sends_to_cloud,
        requires_cloud_consent=sends_to_cloud and not settings.cloud_processing_consent,
    )
    return PreparedSummary(preview=preview, document_text=text)


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
    result = provider.summarize(
        SummaryRequest(
            document_text=prepared.document_text,
            cloud_consent=consent,
            prompt_version="paper-summary-v3",
            allowed_categories=_allowed_categories(settings),
        )
    )
    return SummaryExecution(preview=prepared.preview, result=result)


def _allowed_categories(settings: AppSettings) -> tuple[str, ...]:
    """Limit AI classification to the bundled taxonomy, narrowed by preference."""

    try:
        names = taxonomy_category_names()
    except TaxonomyError:
        return ()
    focus = [name.strip() for name in settings.focus_categories if name.strip()]
    if focus:
        chosen = {name for name in focus if name in names}
        return tuple(name for name in names if name in chosen)
    return tuple(names)


def _selected_page_indexes(page_count: int, mode: SummaryMode) -> tuple[int, ...]:
    if mode is SummaryMode.FULL:
        return tuple(range(page_count))
    front = list(range(min(page_count, 8)))
    tail_start = max(8, page_count - 3)
    return tuple(front + list(range(tail_start, page_count)))


def _selected_model(settings: AppSettings) -> str:
    if settings.summary_provider == "ollama":
        return settings.selected_model.strip()
    if settings.summary_provider == "openai":
        return settings.openai_model.strip()
    return settings.anthropic_model.strip()


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    front = int(max_chars * 0.72)
    marker = "\n\n[...중간 본문 생략...]\n\n"
    back = max_chars - front - len(marker)
    return text[:front] + marker + text[-back:], True
