import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import fitz

from paper_organizer.application.summary_service import (
    SummaryMode,
    SummaryPreparationError,
    prepare_summary,
    prepare_text_summary,
    run_prepared_summary,
)
from paper_organizer.infra.settings import AppSettings
from paper_organizer.providers import CloudConsentRequiredError


SUMMARY = {
    "summary_ko": "시험용 요약",
    "research_question": "시험 질문",
    "methods": ["시험 방법"],
    "contributions": ["시험 기여"],
    "limitations": ["시험 한계"],
    "keywords": ["시험"],
    "title": "시험 제목",
    "authors": ["시험 저자"],
    "year": "2026",
    "venue": "시험 저널",
    "category": "생물공학",
    "subcategory": "단백질공학",
    "meta_tags": ["효소공학", "단백질 설계"],
    "suggested_category": "",
}


class MemorySecretStore:
    def get(self, provider):
        return "test-api-key"

    def set(self, provider, secret):
        raise AssertionError("not used")

    def delete(self, provider):
        raise AssertionError("not used")


class FakeHttpClient:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def post_json(self, url, headers, payload, timeout_seconds):
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(SUMMARY)}
                    ],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }


def make_pdf(path: Path, page_count: int = 12) -> None:
    document = fitz.open()
    for index in range(page_count):
        page = document.new_page()
        lines = [
            f"Page {index + 1} academic paper methods results evidence line {number}."
            for number in range(24)
        ]
        page.insert_text((50, 60), "\n".join(lines), fontsize=8)
    document.save(path)
    document.close()


class SummaryServiceTests(unittest.TestCase):
    def test_quick_preview_shows_exact_transmission_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            make_pdf(path)
            settings = AppSettings(summary_provider="openai", openai_model="gpt-test")
            prepared = prepare_summary(path, settings, SummaryMode.QUICK)

        self.assertEqual(
            prepared.preview.included_pdf_pages,
            (1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12),
        )
        self.assertTrue(prepared.preview.sends_to_cloud)
        self.assertTrue(prepared.preview.requires_cloud_consent)
        self.assertGreater(prepared.preview.estimated_input_tokens, 0)
        self.assertNotIn("academic paper", repr(prepared))

    def test_full_preview_includes_every_page(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            make_pdf(path, page_count=4)
            settings = AppSettings(
                summary_provider="ollama", selected_model="qwen-test"
            )
            prepared = prepare_summary(path, settings, SummaryMode.FULL)

        self.assertEqual(prepared.preview.included_pdf_pages, (1, 2, 3, 4))
        self.assertFalse(prepared.preview.sends_to_cloud)
        self.assertFalse(prepared.preview.requires_cloud_consent)

    def test_qwen_4b_uses_up_to_24k_context_and_trims_to_fit(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:4b",
            resource_profile="balanced",
            hardware_profile={"memory_total_gb": 16, "gpus": []},
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Main academic evidence and methods. " * 4_000],
            settings,
            SummaryMode.FULL,
        )

        self.assertEqual(prepared.preview.context_window, 24_576)
        self.assertTrue(prepared.preview.truncated)
        self.assertLessEqual(
            prepared.preview.estimated_input_tokens,
            24_576 - 3_000,
        )

    def test_qwen_8b_performance_uses_40k_context_when_hardware_allows(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
            resource_profile="performance",
            hardware_profile={"memory_total_gb": 24, "gpus": []},
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Main academic evidence and methods. " * 2_500],
            settings,
            SummaryMode.FULL,
        )

        self.assertEqual(prepared.preview.context_window, 40_960)
        self.assertFalse(prepared.preview.truncated)

    def test_reference_section_is_kept_out_of_ai_input(self):
        settings = AppSettings(
            summary_provider="ollama", selected_model="qwen-test"
        )
        pages = [
            "Title and authors\n" + "Main study evidence and methods. " * 30,
            "Results and conclusion. " * 35,
            "References\n[1] Unrelated Author. Cited Work. 2020.\n"
            "[2] Another Author. Another Work. 2021.",
        ]

        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            pages,
            settings,
            SummaryMode.FULL,
        )

        self.assertIn("Results and conclusion", prepared.document_text)
        self.assertNotIn("Unrelated Author", prepared.document_text)
        self.assertNotIn("\nReferences", prepared.document_text)
        self.assertNotIn("[PDF PAGE 3]", prepared.document_text)

    def test_early_table_of_contents_reference_line_is_not_cut(self):
        settings = AppSettings(
            summary_provider="ollama", selected_model="qwen-test"
        )
        pages = [
            "Contents\nIntroduction\nMethods\nReferences\n"
            + "Opening context. " * 20,
            "Methods and results remain available. " * 40,
            "Conclusion without a trailing bibliography heading. " * 20,
        ]

        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            pages,
            settings,
            SummaryMode.FULL,
        )

        self.assertIn("Methods and results remain available", prepared.document_text)

    def test_cloud_summary_requires_consent_and_does_not_move_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            make_pdf(path)
            settings = AppSettings(summary_provider="openai", openai_model="gpt-test")
            prepared = prepare_summary(path, settings)
            client = FakeHttpClient()
            with self.assertRaises(CloudConsentRequiredError):
                run_prepared_summary(
                    prepared, settings, MemorySecretStore(), http_client=client
                )
            execution = run_prepared_summary(
                prepared,
                settings,
                MemorySecretStore(),
                allow_cloud_once=True,
                http_client=client,
            )
            still_exists = path.is_file()

        self.assertEqual(len(client.calls), 1)
        self.assertTrue(still_exists)
        self.assertEqual(execution.result.data.summary_ko, "시험용 요약")
        self.assertEqual(execution.provenance["analysis_level"], "quick")

    def test_custom_research_categories_are_sent_to_ai(self):
        settings = AppSettings(
            summary_provider="openai",
            openai_model="gpt-test",
            cloud_processing_consent=True,
            research_categories=["균류생태학", "고문서과학"],
            focus_categories=["고문서과학"],
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Main academic content and evidence. " * 30],
            settings,
            SummaryMode.FULL,
        )
        client = FakeHttpClient()

        run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        instructions = client.calls[0]["payload"]["instructions"]
        self.assertIn("Choose category from exactly this list: 고문서과학", instructions)
        self.assertNotIn("균류생태학, 고문서과학", instructions)

    def test_provider_change_requires_new_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            make_pdf(path)
            original = AppSettings(summary_provider="openai", openai_model="gpt-test")
            prepared = prepare_summary(path, original)
            changed = AppSettings(
                summary_provider="anthropic", anthropic_model="claude-test"
            )
            with self.assertRaisesRegex(SummaryPreparationError, "다시 확인"):
                run_prepared_summary(
                    prepared,
                    changed,
                    MemorySecretStore(),
                    allow_cloud_once=True,
                    http_client=FakeHttpClient(),
                )

    def test_too_little_text_automatically_runs_bundled_ocr(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.pdf"
            document = fitz.open()
            document.new_page()
            document.new_page()
            document.save(path)
            document.close()
            settings = AppSettings(
                summary_provider="ollama", selected_model="qwen-test"
            )
            recognized = [
                "Recognized patent claims and description. " * 30,
                "Recognized patent drawings and examples. " * 20,
            ]
            with patch(
                "paper_organizer.application.background_ocr.ocr_page_texts",
                return_value=recognized,
            ) as ocr:
                prepared = prepare_summary(path, settings)

            ocr.assert_called_once_with(
                path.resolve(),
                page_indexes=(0, 1),
                background=False,
            )
            self.assertIn("Recognized patent claims", prepared.document_text)

    def test_bundled_ocr_failure_is_reported_directly(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.pdf"
            document = fitz.open()
            document.new_page()
            document.new_page()
            document.save(path)
            document.close()
            settings = AppSettings(
                summary_provider="ollama", selected_model="qwen-test"
            )
            with patch(
                "paper_organizer.application.background_ocr.ocr_page_texts",
                side_effect=RuntimeError("worker unavailable"),
            ):
                with self.assertRaisesRegex(
                    SummaryPreparationError, "내장 OCR 실행에 실패"
                ):
                    prepare_summary(path, settings)

    def test_one_page_scan_is_excluded_before_ocr(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "one-page.pdf"
            document = fitz.open()
            document.new_page()
            document.save(path)
            document.close()
            settings = AppSettings(
                summary_provider="ollama", selected_model="qwen-test"
            )
            with patch(
                "paper_organizer.application.background_ocr.ocr_page_texts"
            ) as ocr:
                with self.assertRaisesRegex(SummaryPreparationError, "2페이지 미만"):
                    prepare_summary(path, settings)
            ocr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
