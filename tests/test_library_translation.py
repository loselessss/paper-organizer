import unittest
from pathlib import Path

from paper_organizer.application.library_translation import (
    LibraryTranslationService,
    analysis_translation_source_hash,
)
from paper_organizer.application.library_workflow import (
    EditablePaperMetadata,
    LibraryEntry,
)
from paper_organizer.infra.settings import AppSettings
from paper_organizer.providers.base import SummaryData, SummaryResult


class MemorySecrets:
    def get(self, provider):
        return None


class FakeProvider:
    name = "ollama"
    model = "qwen3:4b"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    def summarize(self, request):
        self.requests.append(request)
        return SummaryResult(
            provider=self.name,
            model=self.model,
            prompt_version=request.prompt_version,
            data=SummaryData.from_section_text(self.outputs.pop(0)),
        )


class FakeWorkflow:
    def __init__(self, model="qwen3:4b"):
        self.saved = []
        self.model = model

    def settings(self):
        return AppSettings(
            summary_provider="ollama",
            selected_model=self.model,
        )

    def save_analysis_translation(self, entry, **values):
        self.saved.append((entry, values))
        return "2026-07-30T02:03:04+00:00"


def entry(record):
    path = Path("paper.paperpack")
    return LibraryEntry(
        pdf_path=path,
        sidecar_path=path,
        metadata=EditablePaperMetadata(title="Paper"),
        work_id="work:test",
        source_variant="publisher",
        record=record,
    )


class LibraryTranslationTests(unittest.TestCase):
    def test_translation_uses_plain_text_stage_and_saves_separate_cache(self):
        workflow = FakeWorkflow()
        provider = FakeProvider(["[요약]\n이 연구는 정확도를 개선했습니다."])
        service = LibraryTranslationService(
            workflow,
            MemorySecrets(),
            provider_factory=lambda *_args, **_kwargs: provider,
        )
        paper = entry(
            {
                "description": {
                    "summary": "This study improved accuracy.",
                    "methods": ["A549 cells were cultured in DMEM."],
                }
            }
        )

        translated = service.translate(paper)

        self.assertEqual(provider.requests[0].stage, "translation")
        self.assertIn("[방법]", provider.requests[0].document_text)
        self.assertIn("정확도를 개선", translated.text)
        self.assertEqual(len(workflow.saved), 1)
        saved = workflow.saved[0][1]
        self.assertEqual(
            saved["expected_source_hash"],
            analysis_translation_source_hash(paper.record),
        )
        self.assertEqual(saved["provider"], "ollama")

    def test_matching_cached_translation_is_reused_but_stale_one_is_ignored(self):
        record = {
            "description": {"summary": "Original analysis"},
        }
        source_hash = analysis_translation_source_hash(record)
        record["translations"] = {
            "analysis": {
                "ko": {
                    "text": "번역된 분석",
                    "source_hash": source_hash,
                    "provider": "ollama",
                    "model": "qwen3:4b",
                    "translated_at": "2026-07-30T02:03:04+00:00",
                }
            }
        }
        service = LibraryTranslationService(FakeWorkflow(), MemorySecrets())
        paper = entry(record)

        self.assertEqual(service.cached(paper).text, "번역된 분석")
        record["description"]["summary"] = "Changed analysis"
        self.assertIsNone(service.cached(paper))

    def test_translation_rejects_local_model_below_4b(self):
        workflow = FakeWorkflow("granite4.1:3b")
        provider = FakeProvider(["번역 결과"])
        service = LibraryTranslationService(
            workflow,
            MemorySecrets(),
            provider_factory=lambda *_args, **_kwargs: provider,
        )
        paper = entry({"description": {"summary": "Original analysis"}})

        with self.assertRaisesRegex(ValueError, "최소 4B.*8B 이상"):
            service.translate(paper)
        self.assertEqual(provider.requests, [])


if __name__ == "__main__":
    unittest.main()
