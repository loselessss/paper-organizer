"""Local Ollama summary provider."""

from __future__ import annotations

from typing import Any, Mapping

from .base import (
    SEARCH_ANSWER_INSTRUCTIONS,
    SEARCH_ANSWER_SCHEMA,
    SEARCH_PLAN_INSTRUCTIONS,
    SEARCH_PLAN_SCHEMA,
    SUMMARY_SCHEMA,
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
    parse_search_plan_json,
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
        options: dict[str, Any] = {"num_predict": request.max_output_tokens}
        if request.context_window is not None:
            options["num_ctx"] = request.context_window
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": system_instructions(request)},
                {"role": "user", "content": request.document_text},
            ],
            "format": SUMMARY_SCHEMA,
            "options": options,
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

    def plan_search(self, request: SearchPlanRequest) -> SearchPlanResult:
        request.validate()
        response = self._chat_json(
            SEARCH_PLAN_INSTRUCTIONS,
            request.question,
            SEARCH_PLAN_SCHEMA,
            {
                "num_predict": request.max_output_tokens,
                "num_ctx": 8_192,
            },
        )
        return SearchPlanResult(
            provider=self.name,
            model=self.model,
            data=parse_search_plan_json(_message_content(response)),
        )

    def answer_search(self, request: SearchAnswerRequest) -> SearchAnswerResult:
        request.validate()
        options: dict[str, Any] = {"num_predict": request.max_output_tokens}
        if request.context_window is not None:
            options["num_ctx"] = request.context_window
        response = self._chat_json(
            SEARCH_ANSWER_INSTRUCTIONS,
            f"QUESTION:\n{request.question}\n\nCANDIDATE CONTEXT:\n{request.context_text}",
            SEARCH_ANSWER_SCHEMA,
            options,
        )
        return SearchAnswerResult(
            provider=self.name,
            model=self.model,
            data=parse_search_answer_json(_message_content(response)),
        )

    def _chat_json(
        self,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        options: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return self._http.post_json(
            self._endpoint,
            {"Content-Type": "application/json"},
            {
                "model": self.model,
                "stream": False,
                "think": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": schema,
                "options": dict(options),
            },
            self._timeout_seconds,
        )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _message_content(response: Mapping[str, Any]) -> str:
    message = response.get("message")
    if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
        raise ProviderError("Ollama response contains no message content")
    return message["content"]
