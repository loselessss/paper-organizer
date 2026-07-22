"""Anthropic Messages API summary provider."""

from __future__ import annotations

from typing import Any, Mapping

from .base import (
    SUMMARY_SCHEMA,
    SYSTEM_INSTRUCTIONS,
    JsonHttpClient,
    ProviderError,
    SummaryRequest,
    SummaryResult,
    parse_summary_json,
    require_api_key,
    require_cloud_consent,
)
from .http import UrllibJsonHttpClient


class AnthropicProvider:
    name = "anthropic"
    is_cloud = True

    def __init__(
        self,
        api_key: str | None,
        model: str = "claude-sonnet-4-6",
        http_client: JsonHttpClient | None = None,
        endpoint: str = "https://api.anthropic.com/v1/messages",
        timeout_seconds: float = 120,
    ) -> None:
        self._api_key = api_key
        self.model = model.strip()
        self._http = http_client or UrllibJsonHttpClient()
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        if not self.model:
            raise ValueError("Anthropic model cannot be empty")

    def summarize(self, request: SummaryRequest) -> SummaryResult:
        request.validate()
        require_cloud_consent(request)
        api_key = require_api_key(self._api_key, "Anthropic")
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_output_tokens,
            "system": SYSTEM_INSTRUCTIONS,
            "messages": [{"role": "user", "content": request.document_text}],
            "output_config": {
                "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}
            },
        }
        response = self._http.post_json(
            self._endpoint,
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
            data=parse_summary_json(text),
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
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
