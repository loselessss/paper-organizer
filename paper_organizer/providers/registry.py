"""Build a configured summary provider without exposing API keys to settings."""

from __future__ import annotations

from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import AppSettings

from .anthropic import AnthropicProvider
from .base import JsonHttpClient, SummaryProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider


def build_provider(
    settings: AppSettings,
    secret_store: SecretStore,
    http_client: JsonHttpClient | None = None,
) -> SummaryProvider:
    settings.validate()
    if settings.summary_provider == "ollama":
        return OllamaProvider(settings.selected_model, http_client=http_client)
    if settings.summary_provider == "openai":
        return OpenAIProvider(
            lambda: secret_store.get("openai"),
            settings.openai_model,
            http_client=http_client,
        )
    return AnthropicProvider(
        lambda: secret_store.get("anthropic"),
        settings.anthropic_model,
        http_client=http_client,
    )
