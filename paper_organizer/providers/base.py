"""Common request, result and validation contracts for summary providers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from paper_organizer.infra.secrets import validate_api_key


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary_ko": {"type": "string"},
        "research_question": {"type": "string"},
        "methods": {"type": "array", "items": {"type": "string"}},
        "contributions": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary_ko",
        "research_question",
        "methods",
        "contributions",
        "limitations",
        "keywords",
    ],
    "additionalProperties": False,
}

SYSTEM_INSTRUCTIONS = (
    "You analyze academic papers. Use only the supplied document text. "
    "Return Korean prose for summary_ko and preserve technical names accurately. "
    "If evidence is missing, use an empty string or empty list instead of guessing."
)

ApiKeySource = str | None | Callable[[], str | None]


class ProviderError(RuntimeError):
    """A safe, user-displayable provider failure without document contents."""


class CloudConsentRequiredError(ProviderError):
    pass


class JsonHttpClient(Protocol):
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    document_text: str
    cloud_consent: bool = False
    max_output_tokens: int = 2_000
    prompt_version: str = "paper-summary-v1"

    def validate(self) -> None:
        if not self.document_text.strip():
            raise ValueError("document_text cannot be empty")
        if not 128 <= self.max_output_tokens <= 32_000:
            raise ValueError("max_output_tokens must be between 128 and 32000")


@dataclass(frozen=True, slots=True)
class SummaryData:
    summary_ko: str
    research_question: str
    methods: tuple[str, ...]
    contributions: tuple[str, ...]
    limitations: tuple[str, ...]
    keywords: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SummaryData":
        expected = set(SUMMARY_SCHEMA["required"])
        if set(raw) != expected:
            missing = sorted(expected - set(raw))
            extra = sorted(set(raw) - expected)
            raise ProviderError(f"Invalid summary fields; missing={missing}, extra={extra}")
        for name in ("summary_ko", "research_question"):
            if not isinstance(raw[name], str):
                raise ProviderError(f"Summary field '{name}' must be a string")
        arrays: dict[str, tuple[str, ...]] = {}
        for name in ("methods", "contributions", "limitations", "keywords"):
            value = raw[name]
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise ProviderError(f"Summary field '{name}' must be a string array")
            arrays[name] = tuple(value)
        return cls(
            summary_ko=raw["summary_ko"],
            research_question=raw["research_question"],
            **arrays,
        )


@dataclass(frozen=True, slots=True)
class SummaryResult:
    provider: str
    model: str
    prompt_version: str
    data: SummaryData
    input_tokens: int | None = None
    output_tokens: int | None = None


class SummaryProvider(Protocol):
    name: str
    model: str
    is_cloud: bool

    def summarize(self, request: SummaryRequest) -> SummaryResult: ...


def parse_summary_json(text: str) -> SummaryData:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("Provider returned invalid JSON") from exc
    if not isinstance(raw, dict):
        raise ProviderError("Provider summary must be a JSON object")
    return SummaryData.from_mapping(raw)


def require_cloud_consent(request: SummaryRequest) -> None:
    if not request.cloud_consent:
        raise CloudConsentRequiredError(
            "Cloud summarization requires explicit consent to transmit document text"
        )


def require_api_key(api_key_source: ApiKeySource, provider: str) -> str:
    raw = api_key_source() if callable(api_key_source) else api_key_source
    try:
        return validate_api_key(provider, raw)
    except ValueError as exc:
        message = str(exc)
        if message == "API key cannot be empty":
            message = f"No {provider} API key is configured"
        raise ProviderError(message) from None
