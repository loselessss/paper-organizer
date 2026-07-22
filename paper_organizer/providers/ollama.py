"""Local Ollama summary provider."""

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
)
from .http import UrllibJsonHttpClient


class OllamaProvider:
    name = "ollama"
    is_cloud = False

    def __init__(
        self,
        model: str,
        http_client: JsonHttpClient | None = None,
        endpoint: str = "http://127.0.0.1:11434/api/chat",
        timeout_seconds: float = 300,
    ) -> None:
        self.model = model.strip()
        self._http = http_client or UrllibJsonHttpClient()
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        if not self.model:
            raise ValueError("Ollama model cannot be empty")

    def summarize(self, request: SummaryRequest) -> SummaryResult:
        request.validate()
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": request.document_text},
            ],
            "format": SUMMARY_SCHEMA,
            "options": {"num_predict": request.max_output_tokens},
        }
        response = self._http.post_json(
            self._endpoint,
            {"Content-Type": "application/json"},
            payload,
            self._timeout_seconds,
        )
        message = response.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise ProviderError("Ollama response contains no message content")
        return SummaryResult(
            provider=self.name,
            model=self.model,
            prompt_version=request.prompt_version,
            data=parse_summary_json(message["content"]),
            input_tokens=_optional_int(response.get("prompt_eval_count")),
            output_tokens=_optional_int(response.get("eval_count")),
        )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
