import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from paper_organizer.application.project_classification import classify_projects
from paper_organizer.application.summary_service import SummaryController
from paper_organizer.providers.base import ProviderError, SummaryRequest
from paper_organizer.providers import OllamaProvider, OpenAIProvider, AnthropicProvider, EmbeddedLlamaProvider
from paper_organizer.infra.settings import load_settings, save_settings
from tests.test_providers import FakeHttpClient
from tests.test_projects import project_fixture
from tests.test_background_analysis import execution, MemorySecrets


PROJECTS = [{"id": "p1", "name": "Protein", "description": "Protein folding"},
            {"id": "p2", "name": "Cancer", "description": "HPV screening"}]


def provider_for(text):
    provider = Mock(name="provider")
    provider.summarize.return_value = SimpleNamespace(data=SimpleNamespace(summary=text))
    return provider


class ProjectClassificationTests(unittest.TestCase):
    def test_multiple_matches_and_no_match(self):
        for ids in (["p1", "p2"], []):
            provider = provider_for(json.dumps({"project_ids": ids}))
            result = classify_projects(provider, PROJECTS, title="Title", summary="Summary",
                consent=False, cancelled=lambda: False)
            self.assertEqual(result, tuple(ids))
            payload = json.loads(provider.summarize.call_args.args[0].document_text)
            self.assertEqual(payload["projects"], PROJECTS)
            self.assertEqual(payload["paper"]["title"], "Title")

    def test_invalid_and_invented_ids_are_rejected(self):
        for text in ('{"project_ids":["unknown"]}', '{"project_ids":"p1"}', 'not json', '{"project_ids":[],"other":true}'):
            with self.subTest(text=text), self.assertRaises(ProviderError):
                classify_projects(provider_for(text), PROJECTS, title="", summary="Text",
                    consent=False, cancelled=lambda: False)

    def test_cancel_does_not_call_provider(self):
        provider = provider_for('{}')
        with self.assertRaises(ProviderError):
            classify_projects(provider, PROJECTS, title="", summary="Text", consent=False, cancelled=lambda: True)
        provider.summarize.assert_not_called()

    def test_all_providers_return_project_json_without_summary_schema(self):
        text = '{"project_ids":["p1"]}'
        chat = {"choices": [{"message": {"content": text}}]}
        for cls, response in ((OllamaProvider, {"message": {"content": text}}),
                              (EmbeddedLlamaProvider, chat),
                              (OpenAIProvider, {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}),
                              (AnthropicProvider, {"content": [{"type": "text", "text": text}]})):
            with self.subTest(provider=cls.__name__):
                http = FakeHttpClient(response)
                args = (lambda: "test-key", "model") if cls in (OpenAIProvider, AnthropicProvider) else ("model",)
                provider = cls(*args, http_client=http)
                result = provider.summarize(SummaryRequest(document_text="{}", stage="project", cloud_consent=True))
                self.assertEqual(json.loads(result.data.summary)["project_ids"], ["p1"])

    def test_manual_exclusion_and_inclusion_survive_reanalysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = project_fixture(root)
            included = workflow.save_project("Keep")
            excluded = workflow.save_project("Exclude")
            automatic = workflow.save_project("Automatic")
            entry = workflow.list_library()[0]
            workflow.set_project_membership([entry], included, included=True)
            workflow.set_project_membership([entry], excluded, included=False)
            result = replace(execution(root / "missing.pdf"), project_ids=(excluded, automatic, "deleted"), project_classification_status="completed")
            workflow.apply_analysis_result(entry.sidecar_path, result)
            record = workflow.list_library()[0].record
            self.assertEqual(set(record["curation"]["project_ids"]), {included, automatic})
            self.assertEqual(record["analysis"]["project_classification"]["status"], "completed")

    def test_failure_preserves_summary_and_disabled_mode_skips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            settings = load_settings(settings_path)
            settings.summary_provider = "ollama"
            settings.manual_model = settings.selected_model = "qwen3:4b"
            save_settings(settings, settings_path)
            controller = SummaryController(MemorySecrets(), settings_path)
            original = execution(root / "paper.pdf")
            provider = provider_for('invalid')
            provider.name, provider.model = "ollama", "qwen3:4b"
            with patch("paper_organizer.application.summary_service.build_provider", return_value=provider):
                result = controller.classify_projects(original, PROJECTS)
            self.assertEqual(result.project_classification_status, "failed")
            self.assertEqual(result.result, original.result)
            settings.bibliography_only = True
            save_settings(settings, settings_path)
            with patch("paper_organizer.application.summary_service.build_provider") as build:
                self.assertIs(controller.classify_projects(original, PROJECTS), original)
                build.assert_not_called()
