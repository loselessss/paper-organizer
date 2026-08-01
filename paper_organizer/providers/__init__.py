"""Interchangeable local and cloud summarization providers."""

from .anthropic import AnthropicProvider
from .base import (
    BibliographyData,
    BibliographyRequest,
    BibliographyResult,
    CloudConsentRequiredError,
    DocumentTypeData,
    DocumentTypeRequest,
    DocumentTypeResult,
    ProviderError,
    SearchAnswerData,
    SearchAnswerRequest,
    SearchAnswerResult,
    SearchPaperEvidence,
    SearchPlanData,
    SearchPlanRequest,
    SearchPlanResult,
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
    "BibliographyData",
    "BibliographyRequest",
    "BibliographyResult",
    "CloudConsentRequiredError",
    "CloudRequestPolicy",
    "DocumentTypeData",
    "DocumentTypeRequest",
    "DocumentTypeResult",
    "OllamaProvider",
    "OpenAIProvider",
    "ProviderError",
    "SearchAnswerData",
    "SearchAnswerRequest",
    "SearchAnswerResult",
    "SearchPaperEvidence",
    "SearchPlanData",
    "SearchPlanRequest",
    "SearchPlanResult",
    "SummaryData",
    "SummaryProvider",
    "SummaryRequest",
    "SummaryResult",
    "cloud_request_policy",
]
