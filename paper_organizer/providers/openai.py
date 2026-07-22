"""OpenAI Responses API summary provider."""

from __future__ import annotations

from typing import Any, Mapping

from .base import (
    ApiKeySource,
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


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class OpenAIProvider:
    name = "openai"
    is_cloud = True

    def __init__(
        self,
        api_key: ApiKeySource,
        model: str = "gpt-5.6",
        http_client: JsonHttpClient | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        self._api_key_source = api_key
        self.model = model.strip()
        self._http = http_client or UrllibJsonHttpClient()
        self._timeout_seconds = timeout_seconds
        if not self.model:
            raise ValueError("OpenAI model cannot be empty")

    def summarize(self, request: SummaryRequest) -> SummaryResult:
        request.validate()
        require_cloud_consent(request)
        api_key = require_api_key(self._api_key_source, "OpenAI")
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": SYSTEM_INSTRUCTIONS,
            "input": request.document_text,
            "max_output_tokens": request.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "paper_summary",
                    "strict": True,
                    "schema": SUMMARY_SCHEMA,
                }
            },
        }
        response = self._http.post_json(
            OPENAI_RESPONSES_URL,
            {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            payload,
            self._timeout_seconds,
        )
        text = _collect_output_text(response)
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


def _collect_output_text(response: Mapping[str, Any]) -> str:
    output = response.get("output")
    if not isinstance(output, list):
        raise ProviderError("OpenAI response has no output array")
    parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, Mapping)
                and block.get("type") == "output_text"
                and isinstance(block.get("text"), str)
            ):
                parts.append(block["text"])
    if not parts:
        raise ProviderError("OpenAI response contains no text output")
    return "".join(parts)


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
