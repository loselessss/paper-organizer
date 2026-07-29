"""Translate stored library analysis without overwriting its source text."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Callable

from paper_organizer.application.library_workflow import (
    LibraryEntry,
    LibraryWorkflowController,
)
from paper_organizer.infra.secrets import SecretStore
from paper_organizer.providers.base import (
    JsonHttpClient,
    ProviderError,
    SummaryRequest,
)
from paper_organizer.providers.registry import build_provider


TRANSLATION_PROMPT_VERSION = "paper-analysis-translation-v1"
_TRANSLATABLE_FIELDS = (
    ("요약", "summary"),
    ("연구 질문", "research_question"),
    ("방법", "methods"),
    ("핵심 기여", "contributions"),
    ("한계", "limitations"),
    ("키워드", "keywords"),
)


@dataclass(frozen=True, slots=True)
class LibraryTranslation:
    text: str
    source_hash: str
    provider: str
    model: str
    translated_at: str
    prompt_version: str = TRANSLATION_PROMPT_VERSION


class LibraryTranslationService:
    def __init__(
        self,
        workflow: LibraryWorkflowController,
        secret_store: SecretStore,
        *,
        provider_factory: Callable = build_provider,
        http_client: JsonHttpClient | None = None,
    ) -> None:
        self._workflow = workflow
        self._secret_store = secret_store
        self._provider_factory = provider_factory
        self._http_client = http_client

    def has_source(self, entry: LibraryEntry) -> bool:
        return bool(analysis_translation_text(entry.record))

    def cached(self, entry: LibraryEntry) -> LibraryTranslation | None:
        source_hash = analysis_translation_source_hash(entry.record)
        translations = entry.record.get("translations")
        translations = translations if isinstance(translations, dict) else {}
        analysis = translations.get("analysis")
        analysis = analysis if isinstance(analysis, dict) else {}
        raw = analysis.get("ko")
        if not isinstance(raw, dict):
            return None
        text = str(raw.get("text") or "").strip()
        if not text or str(raw.get("source_hash") or "") != source_hash:
            return None
        return LibraryTranslation(
            text=text,
            source_hash=source_hash,
            provider=str(raw.get("provider") or ""),
            model=str(raw.get("model") or ""),
            translated_at=str(raw.get("translated_at") or ""),
            prompt_version=str(
                raw.get("prompt_version") or TRANSLATION_PROMPT_VERSION
            ),
        )

    def translate(self, entry: LibraryEntry) -> LibraryTranslation:
        source_text = analysis_translation_text(entry.record)
        if not source_text:
            raise ValueError("번역할 AI 분석 내용이 없습니다.")
        source_hash = analysis_translation_source_hash(entry.record)
        settings = self._workflow.settings()
        if (
            settings.summary_provider in {"openai", "anthropic"}
            and not settings.cloud_processing_consent
        ):
            raise ProviderError(
                "클라우드 AI 번역에는 요약 엔진 옵션의 논문 텍스트 전송 동의가 필요합니다."
            )
        provider = self._provider_factory(
            settings,
            self._secret_store,
            http_client=self._http_client,
        )
        request = SummaryRequest(
            document_text=source_text,
            cloud_consent=settings.cloud_processing_consent,
            max_output_tokens=max(
                512,
                min(8_000, math.ceil(len(source_text) / 2)),
            ),
            prompt_version=TRANSLATION_PROMPT_VERSION,
            output_language="ko",
            stage="translation",
            advanced_analysis=False,
        )
        result = provider.summarize(request)
        translated = result.data.summary.strip()
        if _needs_korean_retry(source_text, translated):
            result = provider.summarize(
                SummaryRequest(
                    document_text=source_text,
                    cloud_consent=settings.cloud_processing_consent,
                    max_output_tokens=request.max_output_tokens,
                    prompt_version=TRANSLATION_PROMPT_VERSION,
                    output_language="ko",
                    stage="translation",
                    language_retry=True,
                    advanced_analysis=False,
                )
            )
            translated = result.data.summary.strip()
        if not translated or _needs_korean_retry(source_text, translated):
            raise ProviderError("AI가 유효한 한국어 번역문을 반환하지 않았습니다.")
        saved = self._workflow.save_analysis_translation(
            entry,
            expected_source_hash=source_hash,
            text=translated,
            provider=result.provider,
            model=result.model,
            prompt_version=TRANSLATION_PROMPT_VERSION,
        )
        return LibraryTranslation(
            text=translated,
            source_hash=source_hash,
            provider=result.provider,
            model=result.model,
            translated_at=saved,
        )


def analysis_translation_text(record: dict) -> str:
    description = record.get("description")
    description = description if isinstance(description, dict) else {}
    analysis = record.get("analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    sections: list[str] = []
    for label, key in _TRANSLATABLE_FIELDS:
        value = description.get(key)
        if value in (None, "", []):
            value = analysis.get(key)
        if isinstance(value, list):
            body = "\n".join(f"- {item}" for item in value if str(item).strip())
        else:
            body = str(value or "").strip()
        if body:
            sections.append(f"[{label}]\n{body}")
    return "\n\n".join(sections)


def analysis_translation_source_hash(record: dict) -> str:
    source = analysis_translation_text(record)
    return hashlib.sha256(source.encode("utf-8")).hexdigest() if source else ""


def _needs_korean_retry(source: str, translated: str) -> bool:
    source_body = re.sub(r"\[[^\]]+\]", "", source)
    translated_body = re.sub(r"\[[^\]]+\]", "", translated)
    latin_words = re.findall(r"\b[A-Za-z]{3,}\b", source_body)
    return len(latin_words) >= 3 and not re.search(r"[가-힣]", translated_body)
