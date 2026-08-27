"""Build a configured summary provider without exposing API keys to settings."""

from __future__ import annotations

from paper_organizer.core.ollama_residency import resolve_ollama_keep_alive
from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import AppSettings

from .anthropic import AnthropicProvider
from .base import JsonHttpClient, SummaryProvider
from .embedded import EmbeddedLlamaProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider


def build_provider(
    settings: AppSettings,
    secret_store: SecretStore,
    http_client: JsonHttpClient | None = None,
) -> SummaryProvider:
    settings.validate()
    if settings.summary_provider == "local":
        return EmbeddedLlamaProvider(
            settings.selected_model,
            http_client=http_client,
            timeout_seconds=settings.summary_timeout_seconds,
        )
    if settings.summary_provider == "ollama":
        memory_total_gb = settings.hardware_profile.get("memory_total_gb")
        if not isinstance(memory_total_gb, (int, float)):
            memory_total_gb = None
        return OllamaProvider(
            settings.selected_model,
            http_client=http_client,
            timeout_seconds=settings.summary_timeout_seconds,
            keep_alive=resolve_ollama_keep_alive(
                settings.ollama_residency_mode,
                settings.ollama_resident_model,
                settings.selected_model,
                memory_total_gb,
            ),
        )
    if settings.summary_provider == "openai":
        return OpenAIProvider(
            lambda: secret_store.get("openai"),
            settings.openai_model,
            http_client=http_client,
            timeout_seconds=settings.summary_timeout_seconds,
        )
    return AnthropicProvider(
        lambda: secret_store.get("anthropic"),
        settings.anthropic_model,
        http_client=http_client,
        timeout_seconds=settings.summary_timeout_seconds,
    )
