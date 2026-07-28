import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import fitz

from paper_organizer import __version__
from paper_organizer.application.summary_service import (
    ImmediateSummaryController,
    SummaryMode,
    SummaryPreparationError,
    prepare_summary,
    prepare_text_summary,
    run_prepared_summary,
    _paragraphize_summary,
    _title_needs_original_language_retry,
)
from paper_organizer.infra.settings import AppSettings, save_settings
from paper_organizer.providers import CloudConsentRequiredError, ProviderError


SUMMARY = {
    "summary_ko": "시험용 요약",
    "research_question": "시험 질문",
    "methods": ["시험 방법"],
    "contributions": ["시험 기여"],
    "limitations": ["시험 한계"],
    "keywords": ["시험"],
    "title": "Test Paper Title",
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
        if url.endswith("/api/chat"):
            return {
                "message": {"content": json.dumps(SUMMARY)},
                "prompt_eval_count": 100,
                "eval_count": 20,
            }
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
    def test_invalid_json_is_retried_once_with_stricter_instructions(self):
        class InvalidThenValidClient:
            def __init__(self):
                self.calls = []

            def post_json(self, url, headers, payload, timeout_seconds):
                self.calls.append(payload)
                content = (
                    "This is not JSON."
                    if len(self.calls) == 1
                    else json.dumps(SUMMARY)
                )
                return {
                    "message": {"content": content},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Introduction\nMain academic evidence. " * 40],
            settings,
            SummaryMode.FULL,
        )
        client = InvalidThenValidClient()

        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        self.assertEqual(len(client.calls), 2)
        retry_instructions = client.calls[1]["messages"][0]["content"]
        self.assertIn("previous response was not one complete valid JSON", retry_instructions)
        self.assertEqual(execution.json_retry_count, 1)
        self.assertEqual(execution.provenance["json_retry_count"], 1)
        self.assertEqual(execution.provenance["app_version"], __version__)
        self.assertTrue(execution.result.prompt_version.endswith("-json-retry"))

    def test_repeated_invalid_json_reports_a_korean_recovery_hint(self):
        class AlwaysInvalidClient:
            def __init__(self):
                self.calls = 0

            def post_json(self, url, headers, payload, timeout_seconds):
                self.calls += 1
                return {"message": {"content": '{"summary_ko": "unfinished"'}}

        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Introduction\nMain academic evidence. " * 40],
            settings,
            SummaryMode.FULL,
        )
        client = AlwaysInvalidClient()

        with self.assertRaisesRegex(ProviderError, "두 번 연속"):
            run_prepared_summary(
                prepared,
                settings,
                MemorySecretStore(),
                http_client=client,
            )

        self.assertEqual(client.calls, 2)

    def test_translated_korean_title_is_retried_for_english_source(self):
        self.assertTrue(
            _title_needs_original_language_retry(
                "번역된 논문 제목",
                "English scientific source text. " * 20,
            )
        )
        self.assertFalse(
            _title_needs_original_language_retry(
                "한국어 논문 제목",
                "한국어 원문입니다. " * 20,
            )
        )

    def test_title_script_mismatch_retries_final_json_once(self):
        first = dict(SUMMARY, title="번역된 논문 제목")
        corrected = dict(SUMMARY, title="An Original English Title")

        class SequenceClient:
            def __init__(self):
                self.calls = []
                self.responses = [first, corrected]

            def post_json(self, url, headers, payload, timeout_seconds):
                self.calls.append({"url": url, "headers": headers, "payload": payload})
                value = self.responses.pop(0)
                return {
                    "message": {"content": json.dumps(value)},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            [
                "An Original English Title\nA. Author\nSynthetic Journal\n"
                "Introduction\n" + "English scientific evidence. " * 40,
            ],
            settings,
        )
        client = SequenceClient()
        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )
        self.assertEqual(len(client.calls), 2)
        self.assertIn(
            "previous response incorrectly translated",
            client.calls[1]["payload"]["messages"][0]["content"].casefold(),
        )
        self.assertEqual(execution.result.data.title, "An Original English Title")

    def test_small_ollama_model_uses_section_then_synthesis_and_hides_advanced_fields(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:4b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            [
                "An Original English Title\nA. Author\nSynthetic Journal\n"
                "Abstract\n" + "Abstract evidence. " * 40,
                "Introduction\n" + "Question evidence. " * 40,
                "Materials and Methods\n" + "Method evidence. " * 40,
                "Results\n" + "Result evidence. " * 40,
                "Discussion\n" + "Discussion evidence. " * 40,
            ],
            settings,
            SummaryMode.FULL,
        )
        client = FakeHttpClient()
        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        self.assertEqual(prepared.preview.summary_strategy, "hierarchical")
        self.assertEqual(
            len(client.calls), len(prepared.section_contexts) + 1
        )
        self.assertIn(
            "intermediate pass",
            client.calls[0]["payload"]["messages"][0]["content"],
        )
        self.assertIn(
            "final pass",
            client.calls[-1]["payload"]["messages"][0]["content"],
        )
        self.assertEqual(execution.result.data.contributions, ())
        self.assertEqual(execution.result.data.limitations, ())

    def test_8b_ollama_model_uses_one_pass_and_keeps_advanced_fields(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Introduction\n" + "Scientific evidence. " * 40],
            settings,
        )
        client = FakeHttpClient()
        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )
        self.assertEqual(prepared.preview.summary_strategy, "direct")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(execution.result.data.contributions, ("시험 기여",))
        self.assertEqual(execution.result.data.limitations, ("시험 한계",))

    def test_single_block_summary_is_split_into_readable_paragraphs(self):
        value = (
            "First finding was observed. Second finding was measured. "
            "Third finding was reproduced. The study has one limitation. "
            "The conclusion follows from the results."
        )
        normalized = _paragraphize_summary(value)
        self.assertGreaterEqual(normalized.count("\n\n"), 1)
        self.assertEqual(normalized.replace("\n\n", " "), value)

    def test_immediate_local_summary_starts_ollama_before_request(self):
        with tempfile.TemporaryDirectory() as temp:
            settings_path = Path(temp) / "settings.json"
            settings = AppSettings(
                summary_provider="ollama",
                selected_model="qwen3:4b",
            )
            save_settings(settings, settings_path)
            prepared = prepare_text_summary(
                Path("paper.paperpack"),
                ["Main academic content and evidence. " * 30],
                settings,
            )
            starts = []
            controller = ImmediateSummaryController(
                MemorySecretStore(),
                settings_path,
                ollama_starter=lambda: bool(starts.append(True) or True),
            )
            expected = object()
            with patch(
                "paper_organizer.application.summary_service.run_prepared_summary",
                return_value=expected,
            ) as run:
                result = controller.run(prepared)

        self.assertIs(result, expected)
        self.assertEqual(starts, [True])
        run.assert_called_once()

    def test_immediate_local_summary_reports_ollama_restart_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            settings_path = Path(temp) / "settings.json"
            settings = AppSettings(
                summary_provider="ollama",
                selected_model="qwen3:4b",
            )
            save_settings(settings, settings_path)
            prepared = prepare_text_summary(
                Path("paper.paperpack"),
                ["Main academic content and evidence. " * 30],
                settings,
            )
            controller = ImmediateSummaryController(
                MemorySecretStore(),
                settings_path,
                ollama_starter=lambda: False,
            )
            with patch(
                "paper_organizer.application.summary_service.run_prepared_summary"
            ) as run:
                with self.assertRaisesRegex(
                    SummaryPreparationError, "Ollama 서버를 시작할 수 없습니다"
                ):
                    controller.run(prepared)

        run.assert_not_called()

    def test_quick_preview_shows_exact_transmission_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            make_pdf(path)
            settings = AppSettings(summary_provider="openai", openai_model="gpt-test")
            prepared = prepare_summary(path, settings, SummaryMode.QUICK)

        self.assertEqual(
            prepared.preview.included_pdf_pages,
            (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
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

    def test_phi4_mini_uses_catalog_size_for_small_model_policy(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="phi4-mini",
            hardware_profile={"memory_total_gb": 16, "gpus": []},
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Introduction\nMain academic evidence and methods. " * 100],
            settings,
            SummaryMode.FULL,
        )

        self.assertEqual(prepared.preview.summary_strategy, "hierarchical")
        self.assertEqual(prepared.preview.context_window, 16_384)

    def test_truncation_keeps_each_scientific_section(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:4b",
            hardware_profile={"memory_total_gb": 8, "gpus": []},
        )
        pages = [
            "Introduction\n" + "Intro evidence. " * 3_000,
            "Materials and Methods\n" + "Method evidence. " * 3_000,
            "Results\n" + "Result evidence. " * 3_000,
            "Discussion\n" + "Discussion evidence. " * 3_000,
        ]
        prepared = prepare_text_summary(
            Path("paper.paperpack"), pages, settings, SummaryMode.FULL
        )
        for heading in (
            "Introduction",
            "Materials and Methods",
            "Results",
            "Discussion",
        ):
            self.assertIn(f"[SECTION: {heading}", prepared.document_text)

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

    def test_source_language_setting_is_sent_to_provider(self):
        settings = AppSettings(
            summary_provider="openai",
            openai_model="gpt-test",
            cloud_processing_consent=True,
            summary_language="source",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Introduction\nMain academic content and evidence. " * 30],
            settings,
            SummaryMode.FULL,
        )
        client = FakeHttpClient()

        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        instructions = client.calls[0]["payload"]["instructions"]
        self.assertIn("paper's original language", instructions)
        self.assertEqual(execution.provenance["output_language"], "source")

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
