"""Low-resource, restart-safe execution of queued AI paper summaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.application.summary_service import (
    ImmediateSummaryController,
    SummaryMode,
)
from paper_organizer.infra.ollama_runtime import (
    OllamaRuntimeInspector,
    OllamaRuntimeStatus,
)
from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import default_settings_path, load_settings


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
        summary: ImmediateSummaryController,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        ollama: OllamaRuntimeInspector | None = None,
    ) -> None:
        self._workflow = workflow
        self._summary = summary
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._ollama = ollama or OllamaRuntimeInspector()

    def recover_interrupted(self) -> int:
        return self._workflow.recover_interrupted_analysis()

    def poll_interval(self) -> int:
        return poll_interval_seconds(load_settings(self._settings_path).resource_profile)

    def readiness(self) -> AnalysisReadiness:
        settings = load_settings(self._settings_path)
        provider = settings.summary_provider
        if provider == "ollama":
            model = settings.selected_model.strip()
            if not model:
                return AnalysisReadiness(False, "Ollama 모델을 먼저 선택하세요.")
            status = self._ollama.inspect()
            if not status.reachable:
                return AnalysisReadiness(False, "Ollama가 실행될 때까지 기다리는 중입니다.")
            if not _model_installed(status, model):
                return AnalysisReadiness(False, f"Ollama 모델 {model}이 설치되지 않았습니다.")
            return AnalysisReadiness(True, f"로컬 Ollama {model} 준비됨")
        if not settings.cloud_processing_consent:
            return AnalysisReadiness(
                False,
                "백그라운드 클라우드 분석에는 AI 설정의 지속 전송 동의가 필요합니다.",
            )
        if not self._secret_store.get(provider):
            return AnalysisReadiness(False, f"{provider} API 키가 등록되지 않았습니다.")
        return AnalysisReadiness(True, f"{provider} 백그라운드 분석 준비됨")

    def run_next(self, *, force: bool = False, on_start=None) -> AnalysisRunEvent:
        settings = load_settings(self._settings_path)
        if not force and not settings.background_analysis_enabled:
            return AnalysisRunEvent("disabled", "백그라운드 분석이 중지되어 있습니다.")
        pending = [
            item
            for item in self._workflow.analysis_queue()
            if item.status == "organized_pending_analysis"
        ]
        if not pending:
            return AnalysisRunEvent("idle", "분석할 정리된 논문이 없습니다.")
        readiness = self.readiness()
        if not readiness.ready:
            return AnalysisRunEvent("waiting", readiness.reason)
        item = self._workflow.claim_next_analysis()
        if item is None:
            return AnalysisRunEvent("idle", "다른 작업이 대기열을 갱신했습니다.")
        if on_start is not None:
            on_start(
                AnalysisRunEvent(
                    "started",
                    f"{item.title} 분석을 시작했습니다.",
                    item.queue_id,
                    item.title,
                )
            )
        try:
            pdf = self._workflow.materialize_pdf(Path(item.path))
            mode = (
                SummaryMode.QUICK
                if settings.resource_profile == "eco"
                else SummaryMode.FULL
            )
            prepared = self._summary.prepare(pdf, mode)
            execution = self._summary.run(prepared)
            self._workflow.apply_analysis_result(Path(item.path), execution)
            self._workflow.complete_analysis(item.queue_id)
            return AnalysisRunEvent(
                "completed",
                f"{item.title} 분석과 PaperPack 저장을 완료했습니다.",
                item.queue_id,
                item.title,
            )
        except Exception as exc:
            message = _safe_error(exc)
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


def poll_interval_seconds(resource_profile: str) -> int:
    return {"eco": 30, "balanced": 10, "performance": 2}.get(
        resource_profile, 30
    )


def _model_installed(status: OllamaRuntimeStatus, selected: str) -> bool:
    key = selected.casefold().removesuffix(":latest")
    return any(
        model.name.casefold().removesuffix(":latest") == key
        for model in status.models
    )


def _safe_error(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:500] or exc.__class__.__name__
