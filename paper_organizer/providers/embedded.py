"""Embedded local GGUF summary provider."""

from __future__ import annotations

from typing import Any, Mapping

from .base import (
    BIBLIOGRAPHY_SCHEMA,
    BibliographyRequest,
    BibliographyResult,
    DOCUMENT_TYPE_INSTRUCTIONS,
    DOCUMENT_TYPE_SCHEMA,
    DocumentTypeRequest,
    DocumentTypeResult,
    SEARCH_ANSWER_INSTRUCTIONS,
    SEARCH_ANSWER_SCHEMA,
    SEARCH_PLAN_INSTRUCTIONS,
    SEARCH_PLAN_SCHEMA,
    SummaryData,
    bibliography_instructions,
    summary_response_schema,
    system_instructions,
    JsonHttpClient,
    ProviderError,
    SearchAnswerRequest,
    SearchAnswerResult,
    SearchPlanRequest,
    SearchPlanResult,
    SummaryRequest,
    SummaryResult,
    parse_search_answer_json,
    parse_bibliography_json,
    parse_document_type_json,
    parse_search_plan_json,
    parse_summary_json,
)
from .http import UrllibJsonHttpClient


class EmbeddedLlamaProvider:
    """Talk to the app-managed llama.cpp-compatible local server."""

    name = "local"
    is_cloud = False

    def __init__(
        self,
        model: str,
        http_client: JsonHttpClient | None = None,
        endpoint: str = "http://127.0.0.1:11435/v1/chat/completions",
        timeout_seconds: float = 300,
    ) -> None:
        self.model = model.strip()
        self._managed_runtime = http_client is None and endpoint == "http://127.0.0.1:11435/v1/chat/completions"
        self._http = http_client or UrllibJsonHttpClient()
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        if not self.model:
            raise ValueError("Local AI model cannot be empty")

    def summarize(self, request: SummaryRequest) -> SummaryResult:
        request.validate()
        response = self._chat(
            system_instructions(request),
            request.document_text,
            max_output_tokens=request.max_output_tokens,
            context_window=request.context_window,
            schema=(
                None
                if request.stage in {"section", "translation"}
                else summary_response_schema(request)
            ),
        )
        content = _message_content(response)
        return SummaryResult(
            provider=self.name,
            model=self.model,
            prompt_version=request.prompt_version,
            data=(
                SummaryData.from_section_text(content)
                if request.stage in {"section", "translation"}
                else parse_summary_json(
                    content,
                    advanced_analysis=request.advanced_analysis,
                )
            ),
            input_tokens=_usage_int(response, "prompt_tokens"),
            output_tokens=_usage_int(response, "completion_tokens"),
        )

    def extract_bibliography(
        self, request: BibliographyRequest
    ) -> BibliographyResult:
        request.validate()
        response = self._chat(
            bibliography_instructions(request),
            request.document_text,
            max_output_tokens=request.max_output_tokens,
            context_window=request.context_window,
            schema=BIBLIOGRAPHY_SCHEMA,
        )
        return BibliographyResult(
            provider=self.name,
            model=self.model,
            prompt_version=request.prompt_version,
            data=parse_bibliography_json(_message_content(response)),
            input_tokens=_usage_int(response, "prompt_tokens"),
            output_tokens=_usage_int(response, "completion_tokens"),
        )

    def classify_document_type(
        self, request: DocumentTypeRequest
    ) -> DocumentTypeResult:
        request.validate()
        response = self._chat(
            DOCUMENT_TYPE_INSTRUCTIONS,
            request.document_text,
            max_output_tokens=request.max_output_tokens,
            context_window=request.context_window,
            schema=DOCUMENT_TYPE_SCHEMA,
        )
        return DocumentTypeResult(
            provider=self.name,
            model=self.model,
            prompt_version="paper-document-type-v1",
            data=parse_document_type_json(_message_content(response)),
            input_tokens=_usage_int(response, "prompt_tokens"),
            output_tokens=_usage_int(response, "completion_tokens"),
        )

    def plan_search(self, request: SearchPlanRequest) -> SearchPlanResult:
        request.validate()
        response = self._chat(
            SEARCH_PLAN_INSTRUCTIONS,
            request.question,
            max_output_tokens=request.max_output_tokens,
            context_window=8_192,
            schema=SEARCH_PLAN_SCHEMA,
        )
        return SearchPlanResult(
            provider=self.name,
            model=self.model,
            data=parse_search_plan_json(_message_content(response)),
        )

    def answer_search(self, request: SearchAnswerRequest) -> SearchAnswerResult:
        request.validate()
        response = self._chat(
            SEARCH_ANSWER_INSTRUCTIONS,
            f"QUESTION:\n{request.question}\n\nCANDIDATE CONTEXT:\n{request.context_text}",
            max_output_tokens=request.max_output_tokens,
            context_window=request.context_window,
            schema=SEARCH_ANSWER_SCHEMA,
        )
        return SearchAnswerResult(
            provider=self.name,
            model=self.model,
            data=parse_search_answer_json(_message_content(response)),
        )

    def _chat(
        self,
        system: str,
        user: str,
        *,
        max_output_tokens: int,
        context_window: int | None,
        schema: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        if self._managed_runtime:
            from paper_organizer.infra.embedded_llm_runtime import wait_until_ready

            if not wait_until_ready(min(120, self._timeout_seconds)):
                raise ProviderError("내장 AI 런타임이 준비되지 않았습니다. 모델과 GPU 드라이버 상태를 확인하세요.")
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "seed": 0,
            "max_tokens": max_output_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if context_window is not None:
            payload["n_ctx"] = context_window
        if schema is not None:
            payload["response_format"] = {"type": "json_object", "schema": schema}
        return self._http.post_json(
            self._endpoint,
            {"Content-Type": "application/json"},
            payload,
            self._timeout_seconds,
        )


def _message_content(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, Mapping):
            message = first.get("message")
            if isinstance(message, Mapping) and isinstance(
                message.get("content"), str
            ):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    raise ProviderError("Local AI response contains no message content")


def _usage_int(response: Mapping[str, Any], name: str) -> int | None:
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        return None
    value = usage.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
