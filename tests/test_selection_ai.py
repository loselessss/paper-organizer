"""Tests for ephemeral sPDF selection AI actions."""

import tempfile
import unittest
from pathlib import Path

from paper_organizer.application.selection_ai import SelectionAiService
from paper_organizer.infra.settings import AppSettings, save_settings
from paper_organizer.integrations.spdf_bridge import SpdfSelection
from paper_organizer.providers.base import SummaryData, SummaryResult


class NoSecrets:
    def get(self, _provider):
        return None


class FakeProvider:
    def __init__(self):
        self.requests = []

    def summarize(self, request):
        self.requests.append(request)
        return SummaryResult(
            provider="ollama",
            model="test-model",
            prompt_version=request.prompt_version,
            data=SummaryData.from_section_text("선택 영역 결과"),
        )


class SelectionAiTests(unittest.TestCase):
    def test_only_selected_text_is_sent_and_nothing_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            settings_path = Path(temp) / "settings.json"
            save_settings(
                AppSettings(summary_provider="ollama", selected_model="test-model"),
                settings_path,
            )
            provider = FakeProvider()
            service = SelectionAiService(
                NoSecrets(),
                settings_path,
                provider_factory=lambda *_args, **_kwargs: provider,
            )
            selection = SpdfSelection(
                text="only these words",
                pdf_page=4,
                bounding_boxes=((1, 2, 3, 4),),
                document_id="paper-1",
                document_path=Path("paper.pdf"),
            )
            result = service.run(selection, "summarize")
            self.assertEqual(provider.requests[0].document_text, "only these words")
            self.assertEqual(result.pdf_page, 4)
            self.assertEqual(result.text, "선택 영역 결과")

    def test_ocr_selection_requires_explicit_ocr_first(self):
        selection = SpdfSelection(
            text="",
            pdf_page=1,
            bounding_boxes=(),
            document_id="paper-1",
            document_path=Path("paper.pdf"),
            requires_ocr=True,
        )
        with self.assertRaisesRegex(ValueError, "OCR"):
            SelectionAiService(NoSecrets()).run(selection, "translate")


if __name__ == "__main__":
    unittest.main()
