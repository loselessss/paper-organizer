"""Interchangeable local and cloud summarization providers."""

from .anthropic import AnthropicProvider
from .base import (
    CloudConsentRequiredError,
    ProviderError,
    SummaryData,
    SummaryProvider,
    SummaryRequest,
    SummaryResult,
)
from .ollama import OllamaProvider
from .openai import OpenAIProvider
from .policy import CloudRequestPolicy, cloud_request_policy

__all__ = [
    "AnthropicProvider",
    "CloudConsentRequiredError",
    "CloudRequestPolicy",
    "OllamaProvider",
    "OpenAIProvider",
    "ProviderError",
    "SummaryData",
    "SummaryProvider",
    "SummaryRequest",
    "SummaryResult",
    "cloud_request_policy",
]
