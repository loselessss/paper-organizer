import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest import mock

import fitz

from paper_organizer.application.analysis_queue import AnalysisQueueItem
from paper_organizer.application.background_analysis import BackgroundAnalysisService
from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.application.summary_service import (
    PreparedSummary,
    RegexSummaryFallback,
    SummaryExecution,
    SummaryMode,
    SummaryPreview,
    SummaryRetryExhaustedError,
)
from paper_organizer.core.paperpack import (
    import_pdf_to_paperpack,
    load_paperpack_metadata,
)
from paper_organizer.infra.ollama_runtime import (
    InstalledOllamaModel,
    OllamaRuntimeStatus,
    RunningOllamaModel,
)
from paper_organizer.infra.settings import AppSettings, save_settings
from paper_organizer.providers.base import SummaryData, SummaryResult


class MemorySecrets:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, provider):
        return self.values.get(provider)


class FakeOllama:
    def __init__(self, reachable=True, installed=True):
        models = (
            InstalledOllamaModel("qwen3:4b", 2.5, "4B", "Q4", ""),
        ) if installed else ()
        self.status = OllamaRuntimeStatus(reachable, "test", models, "")

    def inspect(self):
        return self.status


def execution(path: Path, mode=SummaryMode.QUICK):
    preview = SummaryPreview(
        pdf_path=path,
        mode=mode,
        provider="ollama",
        model="qwen3:4b",
        page_count=1,
        included_pdf_pages=(1,),
        character_count=1000,
        estimated_input_tokens=250,
        truncated=False,
        sends_to_cloud=False,
        requires_cloud_consent=False,
    )
    result = SummaryResult(
        provider="ollama",
        model="qwen3:4b",
        prompt_version="paper-summary-v1",
        data=SummaryData(
            summary="AI 요약",
            research_question="질문",
            methods=("방법",),
            contributions=("기여",),
            limitations=("한계",),
            keywords=("키워드",),
        ),
        input_tokens=100,
        output_tokens=20,
    )
    return SummaryExecution(preview, result)


class FakeSummary:
    def __init__(self, result):
        self.result = result
        self.modes = []
        self.purposes = []

    def prepare(self, path, mode, *, purpose="manual"):
        self.modes.append(mode)
        self.purposes.append(("prepare", purpose))
        return PreparedSummary(self.result.preview, "document text")

    def run(self, prepared, *, purpose="manual"):
        self.purposes.append(("run", purpose))
        return self.result


class FailingSummary:
    def __init__(self, preview):
        self.preview = preview
        self.modes = []

    def prepare(self, path, mode, *, purpose="manual"):
        self.modes.append(mode)
        self.purposes = getattr(self, "purposes", [])
        self.purposes.append(("prepare", purpose))
        return PreparedSummary(
            self.preview,
            "document text",
            regex_fallback=RegexSummaryFallback(
                abstract="Original abstract text.",
                abstract_pdf_pages=(1,),
                facts=("Year candidates: 2026",),
            ),
        )

    def run(self, prepared, *, purpose="manual"):
        self.purposes.append(("run", purpose))
        raise SummaryRetryExhaustedError(
            "AI가 세 번 연속 올바른 JSON을 만들지 못했습니다.",
            failure_kind="json_validation",
            attempts=3,
        )


class FakeWorkflow:
    def __init__(self, path: Path):
        self.item = AnalysisQueueItem(
            queue_id="sha256:" + "a" * 64,
            path=str(path),
            file_sha256="a" * 64,
            title="Paper",
            status="organized_pending_analysis",
            priority=0,
            added_at="now",
            updated_at="now",
        )
        self.claimed = False
        self.completed = []
        self.removed = []
        self.failed = []
        self.failure_fallbacks = []
        self.applied = []
        self.needs_ocr = False
        self.ocr_completed = []
        self.retried = []
        self.waiting_reasons = []
        self.multiple_document_reason = ""

    def analysis_queue(self):
        return [] if self.claimed else [self.item]

    def claim_next_analysis(self):
        self.claimed = True
        self.item = replace(self.item, last_error="")
        return self.item

    def set_queue_waiting_reason(self, queue_id, message):
        self.waiting_reasons.append((queue_id, message))
        self.item = replace(self.item, last_error=message)
        return self.item

    def paperpack_needs_ocr(self, path):
        return self.needs_ocr

    def mark_multiple_document_if_needed(self, path):
        return self.multiple_document_reason

    def complete_paperpack_ocr(self, path, *, progress=None):
        if progress is not None:
            progress(1, 2)
            progress(2, 2)
        self.needs_ocr = False
        self.ocr_completed.append(path)
        return ["recognized"] * 2

    def materialize_pdf(self, path):
        return path

    def apply_analysis_result(self, path, result):
        self.applied.append((path, result))

    def complete_analysis(self, queue_id):
        self.completed.append(queue_id)

    def remove_from_queue(self, queue_id):
        self.removed.append(queue_id)

    def fail_analysis(self, queue_id, message):
        self.failed.append((queue_id, message))

    def apply_analysis_failure(self, path, prepared, message, **diagnostics):
        self.failure_fallbacks.append(
            (path, prepared.regex_fallback, message, diagnostics)
        )

    def retry_queue_item(self, queue_id, *, high=False):
        self.retried.append((queue_id, high))

    def recover_interrupted_analysis(self):
        return 0


class BackgroundAnalysisTests(unittest.TestCase):
    def test_multiple_document_bundle_is_removed_without_calling_ai(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "bundle.paperpack")
            workflow.multiple_document_reason = "서로 다른 문서 표지 2개"
            summary = FakeSummary(execution(root / "bundle.pdf"))
            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                memory_available_gb=lambda: 32.0,
            )

            event = service.run_next()

        self.assertEqual(event.state, "skipped")
        self.assertEqual(summary.modes, [])
        self.assertEqual(workflow.removed, [workflow.item.queue_id])
        self.assertIn("복수 문서", event.message)

    def test_immediate_stop_discards_result_and_returns_item_to_waiting_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            stopped = Event()
            holder = {}

            class CancellingSummary(FakeSummary):
                def run(self, prepared, *, purpose="manual"):
                    holder["service"].request_cancel()
                    return super().run(prepared, purpose=purpose)

            service = BackgroundAnalysisService(
                workflow,
                CancellingSummary(execution(root / "paper.pdf")),
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                ollama_stopper=lambda: stopped.set() or True,
                memory_available_gb=lambda: 32.0,
            )
            holder["service"] = service

            event = service.run_next()

        self.assertEqual(event.state, "cancelled")
        self.assertEqual(workflow.applied, [])
        self.assertEqual(workflow.failed, [])
        self.assertEqual(workflow.retried, [(workflow.item.queue_id, False)])
        self.assertTrue(stopped.wait(1))
    def test_translation_runs_in_the_same_queue_without_starting_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                ),
                settings_path,
            )
            pack = root / "paper.paperpack"
            pack.write_bytes(b"placeholder")
            workflow = FakeWorkflow(pack)
            workflow.item = replace(
                workflow.item,
                task_type="translation",
                source_hash="source-hash",
            )
            entry = SimpleNamespace(sidecar_path=pack)
            workflow.list_library = lambda: [entry]

            class Translation:
                def __init__(self):
                    self.entries = []

                def translate(self, value):
                    self.entries.append(value)

            translation = Translation()
            summary = FakeSummary(execution(root / "paper.pdf"))
            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                translation=translation,
                memory_available_gb=lambda: 32.0,
            )

            event = service.run_next()

        self.assertEqual(event.state, "translation_completed")
        self.assertEqual(translation.entries, [entry])
        self.assertEqual(summary.modes, [])
        self.assertEqual(workflow.removed, [workflow.item.queue_id])

    def test_managed_ollama_stops_unless_background_residency_is_checked(self):
        for resident, should_stop in ((False, True), (True, False)):
            with self.subTest(resident=resident), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                settings_path = root / "settings.json"
                save_settings(
                    AppSettings(
                        selected_model="qwen3:4b",
                        background_model="qwen3:4b",
                        background_analysis_enabled=True,
                        background_model_resident=resident,
                    ),
                    settings_path,
                )
                workflow = FakeWorkflow(root / "paper.paperpack")
                service = BackgroundAnalysisService(
                    workflow,
                    FakeSummary(execution(root / "paper.pdf")),
                    MemorySecrets(),
                    settings_path,
                    ollama=FakeOllama(),
                    memory_available_gb=lambda: 32.0,
                )

                with mock.patch(
                    "paper_organizer.infra.ollama_installer.stop_managed_runtime"
                ) as stop_runtime:
                    service.run_next()

                self.assertEqual(stop_runtime.called, should_stop)

    def test_unavailable_ollama_waits_without_claiming_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(selected_model="qwen3:4b", background_analysis_enabled=True),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            summary = FakeSummary(execution(root / "paper.pdf"))
            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(reachable=False),
                memory_available_gb=lambda: 32.0,
            )

            event = service.run_next()

        self.assertEqual(event.state, "waiting")
        self.assertFalse(workflow.claimed)
        self.assertEqual(summary.modes, [])

    def test_ready_background_runner_processes_one_item_in_eco_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                    resource_profile="eco",
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            summary = FakeSummary(execution(root / "paper.pdf"))
            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                memory_available_gb=lambda: 32.0,
            )

            event = service.run_next()

        self.assertEqual(event.state, "completed")
        self.assertEqual(summary.modes, [SummaryMode.QUICK])
        self.assertEqual(
            summary.purposes,
            [("prepare", "background"), ("run", "background")],
        )
        self.assertEqual(workflow.removed, [workflow.item.queue_id])
        self.assertEqual(workflow.completed, [])
        self.assertFalse(workflow.failed)

    def test_forced_queue_item_uses_the_manual_model_purpose(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:1.7b",
                    background_model="qwen3:1.7b",
                    manual_model="qwen3:4b",
                    background_analysis_enabled=False,
                    resource_profile="eco",
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            summary = FakeSummary(execution(root / "paper.pdf"))
            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                memory_available_gb=lambda: 32.0,
            )

            event = service.run_next(force=True)

        self.assertEqual(event.state, "completed")
        self.assertEqual(
            summary.purposes,
            [("prepare", "manual"), ("run", "manual")],
        )

    def test_low_memory_keeps_item_pending_with_reason_then_retries_automatically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                    resource_profile="eco",
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            summary = FakeSummary(execution(root / "paper.pdf"))
            available = [1.2]
            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                memory_available_gb=lambda: available[0],
            )

            waiting = service.run_next()
            self.assertEqual(waiting.state, "waiting")
            self.assertFalse(workflow.claimed)
            self.assertEqual(workflow.item.attempt_count, 0)
            self.assertIn("가용 메모리 부족", workflow.item.last_error)
            self.assertIn("현재 1.2GB", workflow.item.last_error)
            self.assertIn("최소 6.5GB", workflow.item.last_error)

            available[0] = 32.0
            completed = service.run_next()

        self.assertEqual(completed.state, "completed")
        self.assertTrue(workflow.claimed)
        self.assertEqual(workflow.item.last_error, "")
        self.assertEqual(summary.modes, [SummaryMode.QUICK])

    def test_loaded_model_waits_below_the_system_memory_reserve(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                    resource_profile="eco",
                ),
                settings_path,
            )
            ollama = FakeOllama()
            ollama.status = replace(
                ollama.status,
                running_models=(
                    RunningOllamaModel("qwen3:4b", "100% GPU", 5.0, 5.0),
                ),
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            service = BackgroundAnalysisService(
                workflow,
                FakeSummary(execution(root / "paper.pdf")),
                MemorySecrets(),
                settings_path,
                ollama=ollama,
                memory_available_gb=lambda: 0.3,
            )

            event = service.run_next()

        self.assertEqual(event.state, "waiting")
        self.assertIn("실행 중인 qwen3:4b", event.message)
        self.assertIn("시스템 여유 0.5GB", event.message)
        self.assertFalse(workflow.claimed)

    def test_loaded_model_runs_with_half_gb_system_memory_reserve(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                    resource_profile="eco",
                ),
                settings_path,
            )
            ollama = FakeOllama()
            ollama.status = replace(
                ollama.status,
                running_models=(
                    RunningOllamaModel("qwen3:4b", "100% GPU", 5.0, 5.0),
                ),
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            service = BackgroundAnalysisService(
                workflow,
                FakeSummary(execution(root / "paper.pdf")),
                MemorySecrets(),
                settings_path,
                ollama=ollama,
                memory_available_gb=lambda: 0.8,
            )

            event = service.run_next()

        self.assertEqual(event.state, "completed")
        self.assertTrue(workflow.claimed)

    def test_eight_gb_pc_keeps_local_analysis_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            service = BackgroundAnalysisService(
                workflow,
                FakeSummary(execution(root / "paper.pdf")),
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                memory_available_gb=lambda: 6.0,
                memory_total_gb=lambda: 8.0,
            )

            event = service.run_next()

        self.assertEqual(event.state, "waiting")
        self.assertIn("8GB급 PC", event.message)
        self.assertIn("OpenAI·Claude API", event.message)
        self.assertFalse(workflow.claimed)

    def test_sixteen_gb_pc_runs_when_model_plus_half_gb_is_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            service = BackgroundAnalysisService(
                workflow,
                FakeSummary(execution(root / "paper.pdf")),
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                memory_available_gb=lambda: 7.0,
                memory_total_gb=lambda: 16.0,
            )

            event = service.run_next()

        self.assertEqual(event.state, "completed")
        self.assertTrue(workflow.claimed)

    def test_pending_local_analysis_starts_ollama_when_needed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            summary = FakeSummary(execution(root / "paper.pdf"))
            ollama = FakeOllama(reachable=False)
            starts = []

            def start_ollama():
                starts.append(True)
                ollama.status = OllamaRuntimeStatus(
                    True,
                    "test",
                    (
                        InstalledOllamaModel(
                            "qwen3:4b",
                            2.5,
                            "4B",
                            "Q4",
                            "",
                        ),
                    ),
                    "",
                )
                return True

            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
                ollama=ollama,
                ollama_starter=start_ollama,
                memory_available_gb=lambda: 32.0,
            )

            event = service.run_next(force=True)

        self.assertEqual(event.state, "completed")
        self.assertEqual(starts, [True])

    def test_failed_ai_run_saves_regex_fallback_and_keeps_failed_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            failed_summary = FailingSummary(execution(root / "paper.pdf").preview)
            service = BackgroundAnalysisService(
                workflow,
                failed_summary,
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(),
                memory_available_gb=lambda: 32.0,
            )

            event = service.run_next()

        self.assertEqual(event.state, "failed")
        self.assertEqual(workflow.failed[0][0], workflow.item.queue_id)
        self.assertEqual(len(workflow.failure_fallbacks), 1)
        _path, fallback, error, diagnostics = workflow.failure_fallbacks[0]
        self.assertEqual(fallback.abstract, "Original abstract text.")
        self.assertIn("세 번 연속", error)
        self.assertEqual(diagnostics["failure_kind"], "json_validation")
        self.assertEqual(diagnostics["request_attempts"], 3)
        self.assertEqual(workflow.removed, [])

    def test_full_ocr_runs_before_ai_readiness_and_reports_each_page(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(selected_model="qwen3:8b", background_analysis_enabled=True),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            workflow.needs_ocr = True
            summary = FakeSummary(execution(root / "paper.pdf"))
            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
                ollama=FakeOllama(reachable=False),
                ollama_starter=lambda: False,
                memory_available_gb=lambda: 32.0,
            )
            started = []
            progress = []

            event = service.run_next(
                on_start=started.append,
                on_progress=progress.append,
            )

        self.assertEqual(event.state, "ocr_completed")
        self.assertEqual(started[0].state, "ocr_started")
        self.assertEqual([item.state for item in progress], ["ocr_progress"] * 2)
        self.assertEqual(workflow.ocr_completed, [root / "paper.paperpack"])
        self.assertFalse(workflow.claimed)
        self.assertEqual(summary.modes, [])

    def test_full_ocr_waits_for_an_8b_ollama_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    background_analysis_enabled=True,
                ),
                settings_path,
            )
            workflow = FakeWorkflow(root / "paper.paperpack")
            workflow.needs_ocr = True
            summary = FakeSummary(execution(root / "paper.pdf"))
            service = BackgroundAnalysisService(
                workflow,
                summary,
                MemorySecrets(),
                settings_path,
            )

            event = service.run_next()

        self.assertEqual(event.state, "waiting")
        self.assertIn("8B 이상", event.message)
        self.assertEqual(workflow.ocr_completed, [])
        self.assertFalse(workflow.claimed)

    def test_paperpack_result_keeps_curated_summary_and_saves_ai_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_dir = root / "input"
            papers = root / "library" / "papers" / "Test" / "General"
            input_dir.mkdir()
            papers.mkdir(parents=True)
            pdf = input_dir / "paper.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((50, 50), "academic paper methods and results " * 30)
            document.save(pdf)
            document.close()
            pack = papers / "paper.paperpack"
            metadata = {
                "schema_version": 2,
                "description": {"summary": "사용자 요약", "methods": []},
                "curation": {
                    "revision": 1,
                    "field_sources": {"description.summary": "user"},
                    "locked_fields": [],
                },
                "workflow": {},
                "provenance": {},
            }
            import_pdf_to_paperpack(pack, pdf, metadata)
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(input_dir=str(input_dir), library_root=str(root / "library")),
                settings_path,
            )
            workflow = LibraryWorkflowController(settings_path)

            workflow.apply_analysis_result(pack, execution(pdf))
            saved = load_paperpack_metadata(pack)

        self.assertEqual(saved["description"]["summary"], "사용자 요약")
        self.assertEqual(saved["description"]["methods"], ["방법"])
        self.assertEqual(saved["analysis"]["summary"], "AI 요약")
        self.assertEqual(saved["workflow"]["analysis_status"], "completed")

    def test_paperpack_failure_keeps_previous_result_but_stores_regex_view(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_dir = root / "input"
            papers = root / "library" / "papers" / "Test" / "General"
            input_dir.mkdir()
            papers.mkdir(parents=True)
            pdf = input_dir / "paper.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((50, 50), "academic paper methods and results " * 30)
            document.save(pdf)
            document.close()
            pack = papers / "paper.paperpack"
            import_pdf_to_paperpack(
                pack,
                pdf,
                {
                    "schema_version": 2,
                    "description": {"summary": "사용자 요약"},
                    "analysis": {
                        "status": "completed",
                        "summary": "이전 AI 요약",
                    },
                    "workflow": {"analysis_status": "completed"},
                },
            )
            settings_path = root / "settings.json"
            save_settings(
                AppSettings(
                    input_dir=str(input_dir),
                    library_root=str(root / "library"),
                ),
                settings_path,
            )
            workflow = LibraryWorkflowController(settings_path)
            prepared = PreparedSummary(
                execution(pdf).preview,
                "document text",
                regex_fallback=RegexSummaryFallback(
                    abstract="Original abstract text.",
                    abstract_pdf_pages=(1, 2),
                    facts=("DOI candidates: 10.1000/test",),
                ),
            )

            workflow.apply_analysis_failure(
                pack,
                prepared,
                "AI가 세 번 연속 올바른 JSON을 만들지 못했습니다.",
                error_type="SummaryRetryExhaustedError",
                failure_kind="json_validation",
                request_attempts=3,
            )
            saved = load_paperpack_metadata(pack)
            first_failure_pack = papers / "first-failure.paperpack"
            import_pdf_to_paperpack(
                first_failure_pack,
                pdf,
                {
                    "schema_version": 2,
                    "description": {},
                    "workflow": {},
                },
            )
            workflow.apply_analysis_failure(
                first_failure_pack,
                prepared,
                "AI가 세 번 연속 올바른 JSON을 만들지 못했습니다.",
                error_type="SummaryRetryExhaustedError",
                failure_kind="json_validation",
                request_attempts=3,
            )
            first_failure = load_paperpack_metadata(first_failure_pack)

        self.assertEqual(saved["description"]["summary"], "사용자 요약")
        self.assertEqual(saved["analysis"]["status"], "completed")
        self.assertEqual(saved["analysis"]["summary"], "이전 AI 요약")
        self.assertEqual(
            saved["analysis"]["last_attempt"]["status"],
            "failed",
        )
        self.assertEqual(
            saved["analysis"]["fallback"]["abstract"],
            "Original abstract text.",
        )
        diagnostics = saved["analysis"]["last_attempt"]["diagnostics"]
        self.assertEqual(diagnostics["failure_kind"], "json_validation")
        self.assertEqual(diagnostics["error_type"], "SummaryRetryExhaustedError")
        self.assertEqual(diagnostics["request_attempts"], 3)
        self.assertEqual(diagnostics["provider"], "ollama")
        self.assertEqual(diagnostics["model"], "qwen3:4b")
        self.assertEqual(saved["workflow"]["analysis_status"], "failed")
        self.assertTrue(saved["workflow"]["needs_reanalysis"])
        self.assertEqual(first_failure["analysis"]["status"], "failed")
        self.assertEqual(
            first_failure["analysis"]["fallback"]["abstract"],
            "Original abstract text.",
        )


if __name__ == "__main__":
    unittest.main()
