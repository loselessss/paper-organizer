"""Anthropic Messages API summary provider."""

from __future__ import annotations

from typing import Any, Mapping

from .base import (
    ApiKeySource,
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
    require_api_key,
    require_cloud_consent,
)
from .http import UrllibJsonHttpClient


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider:
    name = "anthropic"
    is_cloud = True

    def __init__(
        self,
        api_key: ApiKeySource,
        model: str = "claude-sonnet-4-6",
        http_client: JsonHttpClient | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        self._api_key_source = api_key
        self.model = model.strip()
        self._http = http_client or UrllibJsonHttpClient()
        self._timeout_seconds = timeout_seconds
        if not self.model:
            raise ValueError("Anthropic model cannot be empty")

    def summarize(self, request: SummaryRequest) -> SummaryResult:
        request.validate()
        require_cloud_consent(request)
        api_key = require_api_key(self._api_key_source, "Anthropic")
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "system": system_instructions(request),
            "messages": [{"role": "user", "content": request.document_text}],
        }
        if request.stage not in {"section", "translation"}:
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": summary_response_schema(request),
                }
            }
        response = self._http.post_json(
            ANTHROPIC_MESSAGES_URL,
            {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            payload,
            self._timeout_seconds,
        )
        text = _collect_text(response)
        usage = response.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        return SummaryResult(
            provider=self.name,
            model=self.model,
            prompt_version=request.prompt_version,
            data=(
                SummaryData.from_section_text(text)
                if request.stage in {"section", "translation"}
                else parse_summary_json(
                    text,
                    advanced_analysis=request.advanced_analysis,
                )
            ),
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
        )

    def extract_bibliography(
        self, request: BibliographyRequest
    ) -> BibliographyResult:
        request.validate()
        require_cloud_consent(request)
        response = self._structured_message(
            system=bibliography_instructions(request),
            user=request.document_text,
            schema=BIBLIOGRAPHY_SCHEMA,
            max_tokens=request.max_output_tokens,
        )
        usage = response.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        return BibliographyResult(
            provider=self.name,
            model=self.model,
            prompt_version=request.prompt_version,
            data=parse_bibliography_json(_collect_text(response)),
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
        )

    def classify_document_type(
        self, request: DocumentTypeRequest
    ) -> DocumentTypeResult:
        request.validate()
        require_cloud_consent(request)
        response = self._structured_message(
            system=DOCUMENT_TYPE_INSTRUCTIONS,
            user=request.document_text,
            schema=DOCUMENT_TYPE_SCHEMA,
            max_tokens=request.max_output_tokens,
        )
        usage = response.get("usage")
        usage = usage if isinstance(usage, Mapping) else {}
        return DocumentTypeResult(
            provider=self.name,
            model=self.model,
            prompt_version="paper-document-type-v1",
            data=parse_document_type_json(_collect_text(response)),
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
        )

    def plan_search(self, request: SearchPlanRequest) -> SearchPlanResult:
        request.validate()
        require_cloud_consent(request)
        response = self._structured_message(
            system=SEARCH_PLAN_INSTRUCTIONS,
            user=request.question,
            schema=SEARCH_PLAN_SCHEMA,
            max_tokens=request.max_output_tokens,
        )
        return SearchPlanResult(
            provider=self.name,
            model=self.model,
            data=parse_search_plan_json(_collect_text(response)),
        )

    def answer_search(self, request: SearchAnswerRequest) -> SearchAnswerResult:
        request.validate()
        require_cloud_consent(request)
        response = self._structured_message(
            system=SEARCH_ANSWER_INSTRUCTIONS,
            user=(
                f"QUESTION:\n{request.question}\n\n"
                f"CANDIDATE CONTEXT:\n{request.context_text}"
            ),
            schema=SEARCH_ANSWER_SCHEMA,
            max_tokens=request.max_output_tokens,
        )
        return SearchAnswerResult(
            provider=self.name,
            model=self.model,
            data=parse_search_answer_json(_collect_text(response)),
        )

    def _structured_message(
        self,
        *,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        max_tokens: int,
    ) -> Mapping[str, Any]:
        api_key = require_api_key(self._api_key_source, "Anthropic")
        return self._http.post_json(
            ANTHROPIC_MESSAGES_URL,
            {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "output_config": {
                    "format": {"type": "json_schema", "schema": schema}
                },
            },
            self._timeout_seconds,
        )


def _collect_text(response: Mapping[str, Any]) -> str:
    content = response.get("content")
    if not isinstance(content, list):
        raise ProviderError("Anthropic response has no content array")
    parts = [
        block["text"]
        for block in content
        if isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    if not parts:
        raise ProviderError("Anthropic response contains no text output")
    return "".join(parts)


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
