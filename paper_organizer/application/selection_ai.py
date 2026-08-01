"""Run ephemeral AI actions for an sPDF text selection."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Literal

from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import (
    default_settings_path,
    load_settings,
    settings_for_summary_purpose,
)
from paper_organizer.integrations.spdf_bridge import SpdfSelection
from paper_organizer.providers.base import JsonHttpClient, ProviderError, SummaryRequest
from paper_organizer.providers.registry import build_provider


class SelectionAiCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SelectionAiResult:
    action: Literal["translate", "summarize"]
    text: str
    provider: str
    model: str
    prompt_version: str
    pdf_page: int
    document_id: str


class SelectionAiService:
    """Process only selected text and never write a PaperPack."""

    def __init__(
        self,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        *,
        provider_factory: Callable = build_provider,
        http_client: JsonHttpClient | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._provider_factory = provider_factory
        self._http_client = http_client

    def run(
        self,
        selection: SpdfSelection,
        action: Literal["translate", "summarize"],
        *,
        allow_cloud_once: bool = False,
        cancel_event: Event | None = None,
    ) -> SelectionAiResult:
        if action not in {"translate", "summarize"}:
            raise ValueError("지원하지 않는 선택 영역 작업입니다.")
        if selection.requires_ocr or not selection.text.strip():
            raise ValueError("텍스트 레이어가 없습니다. sPDF에서 선택 영역 OCR을 먼저 실행하세요.")
        if cancel_event is not None and cancel_event.is_set():
            raise SelectionAiCancelled("선택 영역 AI 작업이 취소됐습니다.")
        settings = settings_for_summary_purpose(
            load_settings(self._settings_path), "manual"
        )
        cloud = settings.summary_provider in {"openai", "anthropic"}
        consent = settings.cloud_processing_consent or allow_cloud_once
        if cloud and not consent:
            raise ProviderError(
                "선택 영역을 클라우드 AI로 보내려면 이번 요청의 전송 동의가 필요합니다."
            )
        provider = self._provider_factory(
            settings, self._secret_store, http_client=self._http_client
        )
        prompt_version = (
            "selection-translation-v1"
            if action == "translate"
            else "selection-summary-v1"
        )
        result = provider.summarize(
            SummaryRequest(
                document_text=selection.text,
                cloud_consent=consent,
                max_output_tokens=max(256, min(2_000, math.ceil(len(selection.text) / 2))),
                prompt_version=prompt_version,
                output_language="ko" if action == "translate" else settings.summary_language,
                stage="translation" if action == "translate" else "direct",
                advanced_analysis=False,
            )
        )
        if cancel_event is not None and cancel_event.is_set():
            raise SelectionAiCancelled("선택 영역 AI 작업이 취소됐습니다.")
        text = result.data.summary.strip()
        if not text:
            raise ProviderError("선택 영역 AI 결과가 비어 있습니다.")
        return SelectionAiResult(
            action=action,
            text=text,
            provider=result.provider,
            model=result.model,
            prompt_version=prompt_version,
            pdf_page=selection.pdf_page,
            document_id=selection.document_id,
        )
