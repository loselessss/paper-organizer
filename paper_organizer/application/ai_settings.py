"""GUI-neutral AI settings controller with safe credential status reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from paper_organizer.application.local_ai import (
    LocalAiAssessment,
    LocalAiAssessmentService,
)
from paper_organizer.application.embedded_model_manager import (
    EmbeddedModelManagerService,
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
    local_model_for_purpose,
    load_settings,
    save_settings,
)
from paper_organizer.providers.policy import cloud_request_policy


PROVIDER_LABELS = {
    "local": "내장 로컬 AI",
    "openai": "OpenAI API",
    "anthropic": "Anthropic Claude API",
}
LOCAL_PROVIDER_NAMES = {"local", "ollama"}


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
    automatic_analysis_interval_seconds: int
    manual_analysis_interval_seconds: int
    background_model: str
    manual_model: str
    background_model_resident: bool
    ollama_residency_mode: str
    ollama_resident_model: str
    ollama_force_igpu: bool
    bibliography_only: bool = False


class AiSettingsController:
    def __init__(
        self,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        local_ai: LocalAiAssessmentService | None = None,
        embedded_model_manager: EmbeddedModelManagerService | None = None,
        model_manager: OllamaModelManagerService | None = None,
        ollama_starter: Callable[[], bool] | None = None,
        local_runtime_starter: Callable[[], bool] | None = None,
        ollama_igpu_configurer: Callable[[bool], None] | None = None,
        ollama_restarter: Callable[[], bool] | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._local_ai = local_ai or LocalAiAssessmentService(self._settings_path)
        self._embedded_model_manager = (
            embedded_model_manager
            or EmbeddedModelManagerService(self._settings_path)
        )
        self._model_manager = model_manager or OllamaModelManagerService(
            self._settings_path
        )
        self._ollama_starter = ollama_starter
        self._local_runtime_starter = local_runtime_starter or ollama_starter
        self._ollama_igpu_configurer = ollama_igpu_configurer
        self._ollama_restarter = ollama_restarter

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    def view(self) -> AiSettingsView:
        settings = self.settings()
        provider = _normalized_provider(settings.summary_provider)
        is_cloud = provider in {"openai", "anthropic"} and not settings.bibliography_only
        status = (
            get_secret_status(self._secret_store, provider) if is_cloud else None
        )
        policy = cloud_request_policy(settings)
        return AiSettingsView(
            provider=provider,
            provider_label=PROVIDER_LABELS[provider],
            model=_selected_model(settings),
            provider_choices=tuple(
                ProviderChoice(name, label, name not in LOCAL_PROVIDER_NAMES)
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
            automatic_analysis_interval_seconds=(
                settings.automatic_analysis_interval_seconds
            ),
            manual_analysis_interval_seconds=(
                settings.manual_analysis_interval_seconds
            ),
            background_model=local_model_for_purpose(
                settings,
                "background",
            ),
            manual_model=local_model_for_purpose(settings, "manual"),
            background_model_resident=settings.background_model_resident,
            ollama_residency_mode=settings.ollama_residency_mode,
            ollama_resident_model=settings.ollama_resident_model,
            ollama_force_igpu=settings.ollama_force_igpu,
            bibliography_only=settings.bibliography_only,
        )

    def settings(self) -> AppSettings:
        return load_settings(self._settings_path)

    def should_show_ollama_retirement_notice(self) -> bool:
        """Return whether an upgraded user should see the Ollama cleanup note."""

        settings = load_settings(self._settings_path)
        if settings.ollama_retirement_notice_acknowledged:
            return False
        return (
            settings.summary_provider == "ollama"
            or bool(settings.managed_ollama_models)
            or bool(settings.ollama_model_benchmarks)
            or bool(settings.ollama_resident_model.strip())
        )

    def acknowledge_ollama_retirement_notice(self) -> None:
        settings = load_settings(self._settings_path)
        if settings.summary_provider == "ollama":
            settings.summary_provider = "local"
        settings.ollama_retirement_notice_acknowledged = True
        save_settings(settings, self._settings_path)

    def synchronize_ollama_acceleration(self) -> None:
        """Reapply the saved iGPU preference before this app may start Ollama."""

        settings = load_settings(self._settings_path)
        if settings.summary_provider != "ollama":
            return
        configurer = self._ollama_igpu_configurer
        if configurer is None:
            from paper_organizer.infra.ollama_acceleration import (
                configure_ollama_igpu,
            )

            configurer = configure_ollama_igpu
        configurer(settings.ollama_force_igpu)

    def restart_ollama_runtime(self) -> bool:
        """Restart Ollama after the user changes its process-level GPU setting."""

        restarter = self._ollama_restarter
        if restarter is None:
            from paper_organizer.infra.ollama_installer import restart_runtime

            restarter = restart_runtime
        return restarter()

    def start_ollama_runtime(self) -> bool:
        """Ensure the headless Ollama server is running without restarting it."""

        starter = self._ollama_starter
        if starter is None:
            from paper_organizer.infra.ollama_installer import start_runtime

            starter = start_runtime
        return starter()

    def model_for_provider(self, provider: str) -> str:
        normalized = provider.strip().lower()
        normalized = _normalized_provider(normalized)
        if normalized not in PROVIDER_LABELS:
            raise ValueError(f"Unsupported AI provider: {provider}")
        settings = self.settings()
        if normalized == "local":
            return local_model_for_purpose(settings, "background")
        if normalized == "openai":
            return settings.openai_model
        return settings.anthropic_model

    def set_provider(self, provider: str) -> AiSettingsView:
        """Switch only the summary provider, e.g. from the menu bar."""

        normalized = provider.strip().lower()
        normalized = _normalized_provider(normalized)
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
        automatic_analysis_interval_seconds: int | None = None,
        manual_analysis_interval_seconds: int | None = None,
        background_model: str | None = None,
        manual_model: str | None = None,
        background_model_resident: bool | None = None,
        ollama_residency_mode: str | None = None,
        ollama_resident_model: str | None = None,
        ollama_force_igpu: bool | None = None,
        bibliography_only: bool | None = None,
    ) -> AiSettingsView:
        settings = load_settings(self._settings_path)
        if bibliography_only is not None:
            settings.bibliography_only = bibliography_only
        if settings.bibliography_only:
            save_settings(settings, self._settings_path)
            return self.view()
        normalized_provider = provider.strip().lower()
        normalized_provider = _normalized_provider(normalized_provider)
        if normalized_provider not in PROVIDER_LABELS:
            raise ValueError(f"Unsupported AI provider: {provider}")
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("AI model cannot be empty")
        previous_ollama_models = (
            local_model_for_purpose(settings, "background"),
            local_model_for_purpose(settings, "manual"),
        )
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
        if automatic_analysis_interval_seconds is not None:
            settings.automatic_analysis_interval_seconds = (
                automatic_analysis_interval_seconds
            )
        if manual_analysis_interval_seconds is not None:
            settings.manual_analysis_interval_seconds = (
                manual_analysis_interval_seconds
            )
        if ollama_residency_mode is not None:
            settings.ollama_residency_mode = ollama_residency_mode
        if ollama_resident_model is not None:
            settings.ollama_resident_model = ollama_resident_model.strip()
        previous_force_igpu = settings.ollama_force_igpu
        if ollama_force_igpu is not None:
            settings.ollama_force_igpu = bool(ollama_force_igpu)
        if normalized_provider == "local":
            normalized_background = (
                background_model.strip()
                if background_model is not None
                else normalized_model
            )
            normalized_manual = (
                manual_model.strip()
                if manual_model is not None
                else settings.manual_model.strip() or normalized_model
            )
            if not normalized_background or not normalized_manual:
                raise ValueError(
                    "백그라운드 모델과 수동 요약 모델을 모두 선택하세요."
                )
            settings.selected_model = normalized_background
            settings.background_model = normalized_background
            settings.manual_model = normalized_manual
            if background_model_resident is not None:
                settings.background_model_resident = bool(
                    background_model_resident
                )
            settings.ollama_residency_mode = (
                "always"
                if settings.background_model_resident
                else "unload"
            )
            settings.ollama_resident_model = normalized_background
        elif normalized_provider == "openai":
            settings.openai_model = normalized_model
        else:
            settings.anthropic_model = normalized_model
        settings.validate()
        acceleration_changed = settings.ollama_force_igpu != previous_force_igpu
        apply_acceleration = (
            settings.summary_provider == "ollama"
            and ollama_force_igpu is not None
            and (settings.ollama_force_igpu or acceleration_changed)
        )
        if apply_acceleration:
            configurer = self._ollama_igpu_configurer
            if configurer is None:
                from paper_organizer.infra.ollama_acceleration import (
                    configure_ollama_igpu,
                )

                configurer = configure_ollama_igpu
            configurer(settings.ollama_force_igpu)
        try:
            save_settings(settings, self._settings_path)
        except Exception:
            if apply_acceleration and acceleration_changed:
                try:
                    configurer(previous_force_igpu)
                except Exception:
                    pass
            raise
        current_ollama_models = (
            local_model_for_purpose(settings, "background"),
            local_model_for_purpose(settings, "manual"),
        )
        models_changed = any(
            not _same_ollama_model(previous, current)
            for previous, current in zip(
                previous_ollama_models,
                current_ollama_models,
            )
        )
        if (
            normalized_provider == "local"
            and models_changed
            and not self.start_local_runtime()
        ):
            raise RuntimeError(
                "모델 설정은 저장했지만 내장 AI 런타임을 시작하지 못했습니다. "
                "AI 설정에서 모델 파일과 내장 실행 파일 상태를 확인하세요."
            )
        return self.view()

    def scan_local_ai(
        self, profile: str | None = None, provider: str | None = None
    ) -> LocalAiAssessment:
        return self._local_ai.scan(profile=profile, provider=provider)

    def start_local_runtime(self) -> bool:
        """Ensure the app-managed local AI runtime is running."""

        settings = load_settings(self._settings_path)
        if settings.bibliography_only:
            return False
        if self._local_runtime_starter is not None:
            return self._local_runtime_starter()
        from paper_organizer.infra.embedded_llm_runtime import start_runtime

        return start_runtime(settings)

    def ollama_model_snapshot(self):
        return self._model_manager.snapshot()

    def embedded_model_snapshot(self):
        return self._embedded_model_manager.snapshot()

    def plan_embedded_model_download(self, model: str):
        return self._embedded_model_manager.plan_download(model)

    def download_embedded_model(self, model: str, *, on_progress=None, cancel=None):
        return self._embedded_model_manager.download(
            model,
            on_progress=on_progress,
            cancel=cancel,
        )

    def select_embedded_model(
        self,
        model: str,
        *,
        purpose: str = "background",
        start_server: bool = True,
    ) -> AiSettingsView:
        return self.select_ollama_model(
            model,
            purpose=purpose,
            start_server=start_server,
        )

    def delete_embedded_model(self, model: str) -> bool:
        return self._embedded_model_manager.delete(model)

    def installed_ollama_models(self) -> tuple[str, ...]:
        try:
            return self._model_manager.installed_models()
        except RuntimeError as initial_error:
            if not self.start_ollama_runtime():
                raise RuntimeError(
                    "Ollama가 설치되어 있지만 서버를 시작할 수 없습니다. "
                    "설치 상태를 확인한 뒤 새로고침하세요."
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

    def select_ollama_model(
        self,
        model: str,
        *,
        purpose: str = "background",
        start_server: bool = True,
    ) -> AiSettingsView:
        """Persist a verified local model as the active summary model."""

        normalized = model.strip()
        if not normalized:
            raise ValueError("Ollama model cannot be empty")
        if purpose not in {"background", "manual"}:
            raise ValueError("purpose must be background or manual")
        settings = load_settings(self._settings_path)
        previous = local_model_for_purpose(settings, purpose)
        settings.summary_provider = "local"
        if purpose == "background":
            settings.selected_model = normalized
            settings.background_model = normalized
            settings.ollama_resident_model = normalized
        else:
            settings.manual_model = normalized
        save_settings(settings, self._settings_path)
        if (
            start_server
            and not _same_ollama_model(previous, normalized)
            and not self.start_local_runtime()
        ):
            raise RuntimeError(
                "모델 선택은 저장했지만 내장 AI 런타임을 시작하지 못했습니다. "
                "AI 설정에서 모델 파일과 내장 실행 파일 상태를 확인하세요."
            )
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
        normalized = _normalized_provider(normalized)
        if normalized == "local":
            return SecretStatus("local", False, "")
        return get_secret_status(self._secret_store, normalized)

    def delete_api_key(self, provider: str) -> AiSettingsView:
        normalized = provider.strip().lower()
        normalized = _normalized_provider(normalized)
        if normalized == "local":
            raise ValueError("내장 로컬 AI는 API 키를 사용하지 않습니다.")
        self._secret_store.delete(normalized)
        return self.view()


def _selected_model(settings: AppSettings) -> str:
    if settings.summary_provider in LOCAL_PROVIDER_NAMES:
        return settings.selected_model
    if settings.summary_provider == "openai":
        return settings.openai_model
    return settings.anthropic_model


def _normalized_provider(provider: str) -> str:
    return "local" if provider == "ollama" else provider


def _same_ollama_model(left: str, right: str) -> bool:
    return (
        left.strip().casefold().removesuffix(":latest")
        == right.strip().casefold().removesuffix(":latest")
    )
