"""Low-resource, restart-safe execution of queued AI paper summaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.application.library_translation import LibraryTranslationService
from paper_organizer.application.summary_service import (
    SummaryController,
    SummaryMode,
    ollama_model_supports_ocr,
)
from paper_organizer.core.model_recommendation import load_model_catalog
from paper_organizer.infra.hardware import HardwareInspector
from paper_organizer.infra.ollama_runtime import (
    OllamaRuntimeInspector,
    OllamaRuntimeStatus,
)
from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import (
    default_settings_path,
    load_settings,
    settings_for_summary_purpose,
)
from paper_organizer.core.paperpack import (
    PAPERPACK_SUFFIX,
    content_pages,
    load_paperpack_content,
)


@dataclass(frozen=True, slots=True)
class AnalysisReadiness:
    ready: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AnalysisRunEvent:
    state: str
    message: str
    queue_id: str = ""
    title: str = ""


class BackgroundAnalysisService:
    """Run at most one queued paper per call so scheduling stays controllable."""

    def __init__(
        self,
        workflow: LibraryWorkflowController,
        summary: SummaryController,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        ollama: OllamaRuntimeInspector | None = None,
        ollama_starter: Callable[[], bool] | None = None,
        translation: LibraryTranslationService | None = None,
        ollama_stopper: Callable[[], bool] | None = None,
        memory_available_gb: Callable[[], float] | None = None,
        memory_total_gb: Callable[[], float] | None = None,
    ) -> None:
        self._workflow = workflow
        self._summary = summary
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._ollama = ollama or OllamaRuntimeInspector()
        self._ollama_starter = ollama_starter
        self._translation = translation
        self._ollama_stopper = ollama_stopper
        hardware = HardwareInspector()
        self._memory_available_gb = (
            memory_available_gb or hardware.available_memory_gb
        )
        if memory_total_gb is not None:
            self._memory_total_gb = memory_total_gb
        elif memory_available_gb is not None:
            self._memory_total_gb = lambda: max(
                16.0, float(self._memory_available_gb())
            )
        else:
            self._memory_total_gb = hardware.total_memory_gb
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        """Cancel the current result and interrupt an app-managed Ollama process."""

        self._cancel_requested.set()
        from paper_organizer.application.background_ocr import (
            stop_active_ocr_workers,
        )

        Thread(target=stop_active_ocr_workers, daemon=True).start()
        if load_settings(self._settings_path).summary_provider != "ollama":
            return
        stopper = self._ollama_stopper
        if stopper is None:
            from paper_organizer.infra.ollama_installer import stop_managed_runtime

            stopper = stop_managed_runtime
        Thread(target=stopper, daemon=True).start()

    def reset_cancel(self) -> None:
        self._cancel_requested.clear()

    def recover_interrupted(self) -> int:
        recovered = self._workflow.recover_interrupted_analysis()
        cleanup = getattr(self._workflow, "remove_completed_from_queue", None)
        removed = cleanup() if cleanup is not None else 0
        return recovered + removed

    def poll_interval(self, purpose: str = "automatic") -> int:
        settings = load_settings(self._settings_path)
        if purpose == "manual":
            return settings.manual_analysis_interval_seconds
        if purpose == "automatic":
            return settings.automatic_analysis_interval_seconds
        raise ValueError("purpose must be automatic or manual")

    def readiness(self, *, purpose: str = "background") -> AnalysisReadiness:
        settings = settings_for_summary_purpose(
            load_settings(self._settings_path),
            purpose,
        )
        provider = settings.summary_provider
        if provider == "ollama":
            model = settings.selected_model.strip()
            if not model:
                return AnalysisReadiness(False, "Ollama 모델을 먼저 선택하세요.")
            status = self._ollama.inspect()
            if not status.reachable:
                starter = self._ollama_starter
                if starter is None:
                    from paper_organizer.infra.ollama_installer import start_runtime

                    starter = lambda: start_runtime(inspector=self._ollama)
                if starter():
                    status = self._ollama.inspect()
            if not status.reachable:
                return AnalysisReadiness(False, "Ollama가 실행될 때까지 기다리는 중입니다.")
            if not _model_installed(status, model):
                return AnalysisReadiness(False, f"Ollama 모델 {model}이 설치되지 않았습니다.")
            memory_readiness = _ollama_memory_readiness(
                status,
                model,
                self._memory_available_gb,
                self._memory_total_gb,
            )
            if not memory_readiness.ready:
                return memory_readiness
            return AnalysisReadiness(True, f"로컬 Ollama {model} 준비됨")
        if not settings.cloud_processing_consent:
            return AnalysisReadiness(
                False,
                "백그라운드 클라우드 분석에는 AI 설정의 지속 전송 동의가 필요합니다.",
            )
        if not self._secret_store.get(provider):
            return AnalysisReadiness(False, f"{provider} API 키가 등록되지 않았습니다.")
        return AnalysisReadiness(True, f"{provider} 백그라운드 분석 준비됨")

    def run_next(
        self,
        *,
        force: bool = False,
        keep_runtime: bool | Callable[[], bool] = False,
        on_start=None,
        on_progress=None,
    ) -> AnalysisRunEvent:
        purpose = "manual" if force else "background"
        settings = settings_for_summary_purpose(
            load_settings(self._settings_path),
            purpose,
        )
        if not force and not settings.background_analysis_enabled:
            return AnalysisRunEvent("disabled", "백그라운드 분석이 중지되어 있습니다.")
        pending = [
            item
            for item in self._workflow.analysis_queue()
            if item.status == "organized_pending_analysis"
        ]
        if not pending:
            return AnalysisRunEvent("idle", "분석할 정리된 논문이 없습니다.")
        next_item = pending[0]
        queued_path = Path(next_item.path)
        marker = getattr(
            self._workflow,
            "mark_multiple_document_if_needed",
            None,
        )
        bundle_reason = (
            marker(queued_path)
            if next_item.task_type == "analysis" and marker is not None
            else ""
        )
        if bundle_reason:
            self._workflow.remove_from_queue(next_item.queue_id)
            return AnalysisRunEvent(
                "skipped",
                f"{next_item.title}: 복수 문서 묶음으로 표시하고 AI 요약을 건너뛰었습니다.",
                next_item.queue_id,
                next_item.title,
            )
        if (
            next_item.task_type == "analysis"
            and self._workflow.paperpack_needs_ocr(queued_path)
        ):
            if (
                settings.summary_provider == "ollama"
                and not ollama_model_supports_ocr(settings.selected_model)
            ):
                reason = (
                    "OCR 문서는 8B 이상 Ollama 모델에서만 분석합니다. "
                    "AI 설정에서 8B 모델을 선택하세요."
                )
                self._record_waiting_reason(next_item.queue_id, reason)
                return AnalysisRunEvent(
                    "waiting",
                    reason,
                    next_item.queue_id,
                    next_item.title,
                )
            if on_start is not None:
                on_start(
                    AnalysisRunEvent(
                        "ocr_started",
                        f"{next_item.title} 전체 OCR을 시작했습니다.",
                        next_item.queue_id,
                        next_item.title,
                    )
                )
            try:
                self._workflow.complete_paperpack_ocr(
                    queued_path,
                    progress=(
                        lambda done, total: on_progress(
                            AnalysisRunEvent(
                                "ocr_progress",
                                f"{next_item.title} OCR {done}/{total}페이지",
                                next_item.queue_id,
                                next_item.title,
                            )
                        )
                        if on_progress is not None
                        else None
                    ),
                )
                if self._cancel_requested.is_set():
                    return AnalysisRunEvent(
                        "cancelled",
                        f"{next_item.title} OCR을 즉시 중지하고 대기열에 유지했습니다.",
                        next_item.queue_id,
                        next_item.title,
                    )
                return AnalysisRunEvent(
                    "ocr_completed",
                    f"{next_item.title} 전체 OCR을 저장했습니다. AI 분석을 이어갑니다.",
                    next_item.queue_id,
                    next_item.title,
                )
            except Exception as exc:
                if self._cancel_requested.is_set():
                    return AnalysisRunEvent(
                        "cancelled",
                        f"{next_item.title} 작업을 즉시 중지하고 대기열에 유지했습니다.",
                        next_item.queue_id,
                        next_item.title,
                    )
                message = _safe_error(exc)
                try:
                    self._workflow.fail_analysis(next_item.queue_id, message)
                except Exception as queue_exc:
                    message += f" / 큐 상태 기록 실패: {_safe_error(queue_exc)}"
                return AnalysisRunEvent(
                    "failed",
                    message,
                    next_item.queue_id,
                    next_item.title,
                )
        readiness = self.readiness(purpose=purpose)
        if not readiness.ready:
            self._record_waiting_reason(next_item.queue_id, readiness.reason)
            return AnalysisRunEvent(
                "waiting",
                readiness.reason,
                next_item.queue_id,
                next_item.title,
            )
        item = self._workflow.claim_next_analysis()
        if item is None:
            return AnalysisRunEvent("idle", "다른 작업이 대기열을 갱신했습니다.")
        if on_start is not None:
            on_start(
                AnalysisRunEvent(
                    "translation_started" if item.task_type == "translation" else "started",
                    (
                        f"{item.title} AI 번역을 시작했습니다."
                        if item.task_type == "translation"
                        else f"{item.title} 분석을 시작했습니다."
                    ),
                    item.queue_id,
                    item.title,
                )
            )
        prepared = None
        execution = None
        try:
            self._raise_if_cancelled()
            if item.task_type == "translation":
                if self._translation is None:
                    raise RuntimeError("AI 번역 서비스가 준비되지 않았습니다.")
                entry = next(
                    (
                        value
                        for value in self._workflow.list_library()
                        if value.sidecar_path.resolve() == Path(item.path).resolve()
                    ),
                    None,
                )
                if entry is None:
                    raise RuntimeError("번역할 라이브러리 문서를 찾을 수 없습니다.")
                self._translation.translate(entry)
                self._raise_if_cancelled()
                self._workflow.remove_from_queue(item.queue_id)
                return AnalysisRunEvent(
                    "translation_completed",
                    f"{item.title} AI 번역을 완료했습니다.",
                    item.queue_id,
                    item.title,
                )
            mode = (
                SummaryMode.QUICK
                if settings.resource_profile == "eco"
                else SummaryMode.FULL
            )
            queued_path = Path(item.path)
            try:
                content = (
                    load_paperpack_content(queued_path)
                    if queued_path.suffix.casefold() == PAPERPACK_SUFFIX
                    else {}
                )
            except Exception:
                content = {}
            pages = [text for _number, text in content_pages(content)]
            if content.get("ocr_used") and pages:
                prepared = self._summary.prepare_text(
                    queued_path,
                    pages,
                    mode,
                    purpose=purpose,
                )
            else:
                pdf = self._workflow.materialize_pdf(queued_path)
                prepared = self._summary.prepare(
                    pdf,
                    mode,
                    purpose=purpose,
                )
            self._raise_if_cancelled()
            execution = self._summary.run(prepared, purpose=purpose)
            self._raise_if_cancelled()
            self._workflow.apply_analysis_result(Path(item.path), execution)
            self._workflow.remove_from_queue(item.queue_id)
            return AnalysisRunEvent(
                "completed",
                f"{item.title} 분석과 PaperPack 저장을 완료했습니다.",
                item.queue_id,
                item.title,
            )
        except Exception as exc:
            if self._cancel_requested.is_set():
                message = f"{item.title} 작업을 즉시 중지하고 대기열로 되돌렸습니다."
                try:
                    self._workflow.retry_queue_item(item.queue_id)
                except Exception as queue_exc:
                    message += f" / 큐 복구 실패: {_safe_error(queue_exc)}"
                return AnalysisRunEvent(
                    "cancelled",
                    message,
                    item.queue_id,
                    item.title,
                )
            message = _safe_error(exc)
            if item.task_type == "analysis" and prepared is not None and execution is None:
                save_failure = getattr(
                    self._workflow,
                    "apply_analysis_failure",
                    None,
                )
                if save_failure is not None:
                    try:
                        save_failure(
                            Path(item.path),
                            prepared,
                            message,
                            **_failure_diagnostics(exc),
                        )
                    except Exception as fallback_exc:
                        message += (
                            " / 정규식 추출본 저장 실패: "
                            f"{_safe_error(fallback_exc)}"
                        )
            try:
                self._workflow.fail_analysis(item.queue_id, message)
            except Exception as queue_exc:
                message += f" / 큐 상태 기록 실패: {_safe_error(queue_exc)}"
            return AnalysisRunEvent(
                "failed",
                message,
                item.queue_id,
                item.title,
            )
        finally:
            should_keep_runtime = (
                keep_runtime() if callable(keep_runtime) else keep_runtime
            )
            if (
                settings.summary_provider == "ollama"
                and settings.ollama_residency_mode == "unload"
                and not should_keep_runtime
            ):
                from paper_organizer.infra.ollama_installer import stop_managed_runtime

                stop_managed_runtime()

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested.is_set():
            raise RuntimeError("analysis cancelled")

    def _record_waiting_reason(self, queue_id: str, reason: str) -> None:
        recorder = getattr(self._workflow, "set_queue_waiting_reason", None)
        if recorder is None:
            return
        try:
            recorder(queue_id, reason)
        except Exception:
            # A transient queue write problem must not turn a safe wait into a failure.
            pass


def poll_interval_seconds(resource_profile: str) -> int:
    return {"eco": 30, "balanced": 10, "performance": 2}.get(
        resource_profile, 30
    )


def _model_installed(status: OllamaRuntimeStatus, selected: str) -> bool:
    key = _model_key(selected)
    return any(
        _model_key(model.name) == key
        for model in status.models
    )


def _model_key(value: str) -> str:
    return value.strip().casefold().removesuffix(":latest")


def _ollama_memory_readiness(
    status: OllamaRuntimeStatus,
    selected: str,
    available_memory: Callable[[], float],
    total_memory: Callable[[], float],
) -> AnalysisReadiness:
    """Protect the desktop from loading a model when system RAM is exhausted."""

    try:
        available_gb = max(0.0, float(available_memory()))
        total_gb = max(0.0, float(total_memory()))
    except (OSError, TypeError, ValueError):
        return AnalysisReadiness(True, "가용 메모리를 확인하지 못해 분석을 계속합니다.")

    if total_gb < 12.0:
        return AnalysisReadiness(
            False,
            (
                f"시스템 RAM {total_gb:.1f}GB: 8GB급 PC에서는 로컬 AI 분석을 "
                "안전하게 실행할 수 없습니다. OpenAI·Claude API를 사용하거나 "
                "RAM 16GB 이상 PC에서 다시 시도하세요."
            ),
        )

    key = _model_key(selected)
    loaded = any(_model_key(model.name) == key for model in status.running_models)
    reserve_gb = 1.0
    if loaded:
        required_gb = max(1.0, reserve_gb)
        if available_gb >= required_gb:
            return AnalysisReadiness(True, f"가용 메모리 {available_gb:.1f}GB")
        return AnalysisReadiness(
            False,
            (
                f"가용 메모리 부족: 현재 {available_gb:.1f}GB, 실행 중인 "
                f"{selected}을 유지하려면 시스템 여유 {required_gb:.1f}GB가 "
                "필요합니다. 다른 앱을 닫으면 자동으로 다시 시도합니다."
            ),
        )

    runtime_gb = _estimated_runtime_memory_gb(status, selected)
    required_gb = runtime_gb + reserve_gb
    if available_gb >= required_gb:
        return AnalysisReadiness(True, f"가용 메모리 {available_gb:.1f}GB")
    return AnalysisReadiness(
        False,
        (
            f"가용 메모리 부족: 현재 {available_gb:.1f}GB, {selected} 시작 예상 "
            f"{runtime_gb:.1f}GB + 시스템 여유 {reserve_gb:.1f}GB = 최소 "
            f"{required_gb:.1f}GB가 필요합니다. 다른 앱을 닫거나 더 작은 모델을 "
            "선택하면 자동으로 다시 시도합니다."
        ),
    )


def _estimated_runtime_memory_gb(
    status: OllamaRuntimeStatus, selected: str
) -> float:
    key = _model_key(selected)
    try:
        _version, specs = load_model_catalog()
        spec = next(
            (value for value in specs if _model_key(value.model_id) == key),
            None,
        )
        if spec is not None:
            return spec.runtime_memory_gb
    except (OSError, TypeError, ValueError):
        pass
    installed = next(
        (model for model in status.models if _model_key(model.name) == key),
        None,
    )
    if installed is not None and installed.size_gb > 0:
        return max(2.0, installed.size_gb * 1.5)
    return 4.0


def _safe_error(exc: BaseException) -> str:
    from paper_organizer.infra.redaction import redact_text

    return " ".join(redact_text(exc).split())[:500] or exc.__class__.__name__


def _failure_diagnostics(exc: BaseException) -> dict[str, object]:
    """Return safe structured context without traceback, secrets or paper text."""

    message = str(exc).casefold()
    kind = str(getattr(exc, "failure_kind", "") or "")
    if not kind:
        if "json" in message:
            kind = "json_validation"
        elif "언어" in message or "language" in message:
            kind = "language_validation"
        elif "timeout" in message or "timed out" in message or "시간 초과" in message:
            kind = "timeout"
        elif "api 키" in message or "api key" in message or "auth" in message:
            kind = "authentication"
        elif "ollama" in message:
            kind = "ollama_runtime"
        else:
            kind = "provider_or_application"
    attempts = getattr(exc, "attempts", None)
    return {
        "error_type": exc.__class__.__name__,
        "failure_kind": kind,
        "request_attempts": (
            attempts
            if isinstance(attempts, int) and not isinstance(attempts, bool)
            else None
        ),
    }
