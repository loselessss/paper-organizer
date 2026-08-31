"""Verify bibliography-only processing preserves the PaperPack source of truth."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import test_auto_organize as fixtures
from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.application.analysis_queue import AnalysisQueueStore
from paper_organizer.application.background_analysis import BackgroundAnalysisService
from paper_organizer.application.bibliography_lookup import VerifiedBibliography
from paper_organizer.core.paperpack import load_paperpack_metadata, update_paperpack
from paper_organizer.application.library_workflow import rebuild_library_index
from paper_organizer.infra.settings import AppSettings, load_settings, save_settings
from paper_organizer.providers.registry import build_provider
from paper_organizer.providers.base import ProviderError


class BibliographyModeTests(unittest.TestCase):
    def test_can_save_without_model_or_keys_and_keep_provider_preferences(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            secrets = Mock()
            save_settings(AppSettings(summary_provider="openai", openai_model="saved-model"), path)
            controller = AiSettingsController(secrets, path)
            view = controller.save_preferences(provider="local", model="", bibliography_only=True,
                cloud_processing_consent=False, cloud_request_profile="balanced",
                cloud_max_parallel_requests=1, cloud_monthly_budget_usd=0)
            self.assertTrue(view.bibliography_only)
            self.assertEqual(load_settings(path).openai_model, "saved-model")
            self.assertFalse(controller.start_local_runtime())
            secrets.get.assert_not_called()
            with self.assertRaisesRegex(ProviderError, "서지 전용"):
                build_provider(load_settings(path), secrets)

    def test_queue_skips_translation_without_claiming_it(self):
        with tempfile.TemporaryDirectory() as temp:
            queue = AnalysisQueueStore(Path(temp))
            translation = queue.enqueue_translation(path=Path(temp) / "a.paperpack", file_sha256="a"*64, title="A", source_hash="abc")
            analysis = queue.enqueue(path=Path(temp) / "b.paperpack", file_sha256="b"*64, title="B", status="organized_pending_analysis")
            self.assertEqual(queue.claim_next(task_type="analysis").queue_id, analysis.queue_id)
            self.assertEqual(next(i for i in queue.load() if i.queue_id == translation.queue_id).status, "organized_pending_analysis")

    def test_background_without_ai_persists_bibliography_but_preserves_user_and_summary(self):
        for existing_analysis in (False, True):
            with self.subTest(existing_analysis=existing_analysis), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                lookup = fixtures.FakeBibliographyLookup(None)
                workflow, downloads, library = fixtures.AutoOrganizeTests()._controller(root, bibliography_lookup=lookup)
                path = root / "settings.json"
                settings = load_settings(path)
                settings.bibliography_only = True
                settings.selected_model = ""
                save_settings(settings, path)
                fixtures.write_pdf(downloads / "paper.pdf", fixtures.protein_pages())
                workflow.scan()
                workflow.scan()
                pack = next((library / "papers").rglob("*.paperpack"))
                record = load_paperpack_metadata(pack)
                record["bibliography"]["title"] = "User title"
                record["curation"]["field_sources"]["bibliography.title"] = "user"
                if existing_analysis:
                    record["analysis"] = {"status": "completed", "provider": "test"}
                    record["workflow"]["analysis_status"] = "completed"
                    record["workflow"]["updated_at"] = "2026-01-01T00:00:00+00:00"
                    record["description"]["summary"] = "Existing summary"
                prior = copy.deepcopy(record)
                update_paperpack(pack, record)
                lookup.result = VerifiedBibliography(title="External title", authors=("Verified Author",), year=2025, venue="Journal", source="crossref", score=1.0)
                summary, secrets = Mock(), Mock()
                service = BackgroundAnalysisService(workflow, summary, secrets, path)
                self.assertTrue(service.readiness().ready)
                result = service.run_next(force=True)
                self.assertEqual(result.state, "completed", result.message)
                summary.prepare.assert_not_called()
                summary.run.assert_not_called()
                secrets.get.assert_not_called()
                stored = load_paperpack_metadata(pack)
                self.assertEqual(stored["bibliography"]["title"], "User title")
                self.assertEqual(stored["bibliography"]["authors"], ["Verified Author"])
                self.assertEqual(stored.get("analysis"), prior.get("analysis"))
                self.assertEqual(stored.get("description"), prior.get("description"))
                self.assertEqual(stored["workflow"]["bibliography_status"], "verified")
                self.assertEqual(stored["workflow"].get("updated_at"), prior["workflow"].get("updated_at"))
                rebuild_library_index(library)
                workflow.invalidate_library_cache()
                entry = workflow.list_library()[0]
                self.assertEqual(entry.metadata.authors, ["Verified Author"])
                if not existing_analysis:
                    self.assertFalse(entry.analysis_completed_at)
                self.assertEqual(workflow.analysis_queue(), [])


if __name__ == "__main__":
    unittest.main()
