"""GUI-neutral AI settings controller with safe credential status reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from paper_organizer.application.local_ai import (
    LocalAiAssessment,
    LocalAiAssessmentService,
)
from paper_organizer.application.ollama_model_manager import (
    OllamaModelManagerService,
)
from paper_organizer.infra.secrets import (
    SecretStatus,
    SecretStore,
    get_secret_status,
    validate_api_key,
)
from paper_organizer.infra.settings import (
    AppSettings,
    default_settings_path,
    load_settings,
    save_settings,
)
from paper_organizer.providers.policy import cloud_request_policy


PROVIDER_LABELS = {
    "ollama": "로컬 Ollama",
    "openai": "OpenAI API",
    "anthropic": "Anthropic Claude API",
}


@dataclass(frozen=True, slots=True)
class ProviderChoice:
    provider: str
    label: str
    is_cloud: bool


@dataclass(frozen=True, slots=True)
class AiSettingsView:
    provider: str
    provider_label: str
    model: str
    provider_choices: tuple[ProviderChoice, ...]
    key_required: bool
    key_configured: bool
    key_hint: str
    cloud_processing_consent: bool
    cloud_request_profile: str
    effective_parallel_requests: int
    cloud_monthly_budget_usd: float | None
    model_profile: str
    recommended_model: str
    recommendation_profile: str
    last_hardware_scan_at: str
    summary_language: str
    summary_timeout_seconds: int
    ollama_residency_mode: str
    ollama_resident_model: str


class AiSettingsController:
    def __init__(
        self,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        local_ai: LocalAiAssessmentService | None = None,
        model_manager: OllamaModelManagerService | None = None,
        ollama_starter: Callable[[], bool] | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._local_ai = local_ai or LocalAiAssessmentService(self._settings_path)
        self._model_manager = model_manager or OllamaModelManagerService(
            self._settings_path
        )
        self._ollama_starter = ollama_starter

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    def view(self) -> AiSettingsView:
        settings = self.settings()
        provider = settings.summary_provider
        is_cloud = provider in {"openai", "anthropic"}
        status = (
            get_secret_status(self._secret_store, provider) if is_cloud else None
        )
        policy = cloud_request_policy(settings)
        return AiSettingsView(
            provider=provider,
            provider_label=PROVIDER_LABELS[provider],
            model=_selected_model(settings),
            provider_choices=tuple(
                ProviderChoice(name, label, name != "ollama")
                for name, label in PROVIDER_LABELS.items()
            ),
            key_required=is_cloud,
            key_configured=status.configured if status else False,
            key_hint=status.masked_hint if status else "",
            cloud_processing_consent=settings.cloud_processing_consent,
            cloud_request_profile=settings.cloud_request_profile,
            effective_parallel_requests=policy.max_parallel_requests,
            cloud_monthly_budget_usd=policy.monthly_budget_usd,
            model_profile=settings.model_profile,
            recommended_model=settings.recommended_model,
            recommendation_profile=str(
                settings.hardware_profile.get("recommendation_profile") or ""
            ),
            last_hardware_scan_at=settings.last_hardware_scan_at,
            summary_language=settings.summary_language,
            summary_timeout_seconds=settings.summary_timeout_seconds,
            ollama_residency_mode=settings.ollama_residency_mode,
            ollama_resident_model=settings.ollama_resident_model,
        )

    def settings(self) -> AppSettings:
        return load_settings(self._settings_path)

    def model_for_provider(self, provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized not in PROVIDER_LABELS:
            raise ValueError(f"Unsupported AI provider: {provider}")
        settings = self.settings()
        if normalized == "ollama":
            return settings.selected_model
        if normalized == "openai":
            return settings.openai_model
        return settings.anthropic_model

    def set_provider(self, provider: str) -> AiSettingsView:
        """Switch only the summary provider, e.g. from the menu bar."""

        normalized = provider.strip().lower()
        if normalized not in PROVIDER_LABELS:
            raise ValueError(f"Unsupported AI provider: {provider}")
        settings = load_settings(self._settings_path)
        settings.summary_provider = normalized
        save_settings(settings, self._settings_path)
        return self.view()

    def save_preferences(
        self,
        *,
        provider: str,
        model: str,
        cloud_processing_consent: bool,
        cloud_request_profile: str,
        cloud_max_parallel_requests: int,
        cloud_monthly_budget_usd: float,
        model_profile: str | None = None,
        summary_language: str | None = None,
        summary_timeout_seconds: int | None = None,
        ollama_residency_mode: str | None = None,
        ollama_resident_model: str | None = None,
    ) -> AiSettingsView:
        normalized_provider = provider.strip().lower()
        if normalized_provider not in PROVIDER_LABELS:
            raise ValueError(f"Unsupported AI provider: {provider}")
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("AI model cannot be empty")
        settings = load_settings(self._settings_path)
        settings.summary_provider = normalized_provider
        settings.cloud_processing_consent = bool(cloud_processing_consent)
        settings.cloud_request_profile = cloud_request_profile
        settings.cloud_max_parallel_requests = cloud_max_parallel_requests
        settings.cloud_monthly_budget_usd = cloud_monthly_budget_usd
        if model_profile is not None:
            settings.model_profile = model_profile
        if summary_language is not None:
            settings.summary_language = summary_language
        if summary_timeout_seconds is not None:
            settings.summary_timeout_seconds = summary_timeout_seconds
        if ollama_residency_mode is not None:
            settings.ollama_residency_mode = ollama_residency_mode
        if ollama_resident_model is not None:
            settings.ollama_resident_model = ollama_resident_model.strip()
        if normalized_provider == "ollama":
            settings.selected_model = normalized_model
        elif normalized_provider == "openai":
            settings.openai_model = normalized_model
        else:
            settings.anthropic_model = normalized_model
        save_settings(settings, self._settings_path)
        return self.view()

    def scan_local_ai(self, profile: str | None = None) -> LocalAiAssessment:
        return self._local_ai.scan(profile=profile)

    def ollama_model_snapshot(self):
        return self._model_manager.snapshot()

    def installed_ollama_models(self) -> tuple[str, ...]:
        try:
            return self._model_manager.installed_models()
        except RuntimeError as initial_error:
            starter = self._ollama_starter
            if starter is None:
                from paper_organizer.infra.ollama_installer import start_runtime

                starter = start_runtime
            if not starter():
                raise RuntimeError(
                    "Ollama가 설치되어 있지만 실행할 수 없습니다. "
                    "시작 메뉴에서 Ollama를 실행한 뒤 새로고침하세요."
                ) from initial_error
            try:
                return self._model_manager.installed_models()
            except RuntimeError as exc:
                raise RuntimeError(
                    f"Ollama를 시작했지만 설치 모델을 확인하지 못했습니다: {exc}"
                ) from exc

    def plan_ollama_install(self, model: str):
        return self._model_manager.plan_install(model)

    def install_ollama_model(self, model: str, *, on_progress=None, cancel=None):
        return self._model_manager.install(
            model,
            on_progress=on_progress,
            cancel=cancel,
        )

    def verify_installed_ollama_model(self, model: str):
        return self._model_manager.verify_installed(model)

    def select_ollama_model(self, model: str) -> AiSettingsView:
        """Persist a verified local model as the active summary model."""

        normalized = model.strip()
        if not normalized:
            raise ValueError("Ollama model cannot be empty")
        settings = load_settings(self._settings_path)
        settings.summary_provider = "ollama"
        settings.selected_model = normalized
        save_settings(settings, self._settings_path)
        return self.view()

    def delete_ollama_model(self, model: str) -> bool:
        return self._model_manager.delete(model)

    def save_api_key(self, provider: str, api_key: str) -> AiSettingsView:
        normalized = provider.strip().lower()
        value = validate_api_key(normalized, api_key)
        self._secret_store.set(normalized, value)
        return self.view()

    def key_status(self, provider: str) -> SecretStatus:
        normalized = provider.strip().lower()
        if normalized == "ollama":
            return SecretStatus("ollama", False, "")
        return get_secret_status(self._secret_store, normalized)

    def delete_api_key(self, provider: str) -> AiSettingsView:
        normalized = provider.strip().lower()
        if normalized == "ollama":
            raise ValueError("Ollama does not use an API key")
        self._secret_store.delete(normalized)
        return self.view()


def _selected_model(settings: AppSettings) -> str:
    if settings.summary_provider == "ollama":
        return settings.selected_model
    if settings.summary_provider == "openai":
        return settings.openai_model
    return settings.anthropic_model
