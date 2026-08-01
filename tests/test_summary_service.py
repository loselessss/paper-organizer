import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import fitz

from paper_organizer import __version__
from paper_organizer.application.summary_service import (
    SummaryController,
    SummaryMode,
    SummaryPreparationError,
    prepare_summary,
    prepare_text_summary,
    run_prepared_summary,
    _paragraphize_summary,
    _ensure_review_nature_method,
    _publication_year_present,
    _review_methods_supported_by_source,
    _strip_document_type_title_prefix,
)
from paper_organizer.infra.settings import AppSettings, save_settings
from paper_organizer.providers import CloudConsentRequiredError, ProviderError


SUMMARY = {
    "summary": "시험용 요약",
    "research_question": "시험 질문",
    "methods": ["시험 방법"],
    "contributions": ["시험 기여"],
    "limitations": ["시험 한계"],
    "keywords": ["시험"],
    "category": "생물공학",
    "subcategory": "단백질공학",
    "meta_tags": ["효소공학", "단백질 설계"],
    "suggested_category": "",
}
BIBLIOGRAPHY = {
    "title": "Test Paper Title",
    "authors": ["시험 저자"],
    "year": "2026",
    "venue": "시험 저널",
}


class MemorySecretStore:
    def get(self, provider):
        return "test-api-key"

    def set(self, provider, secret):
        raise AssertionError("not used")

    def delete(self, provider):
        raise AssertionError("not used")


class FakeHttpClient:
    def __init__(self, summary=None, bibliography=None):
        self.calls: list[dict[str, Any]] = []
        self.summary = summary or SUMMARY
        self.bibliography = bibliography or BIBLIOGRAPHY

    def post_json(self, url, headers, payload, timeout_seconds):
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        schema = (
            payload.get("format")
            if url.endswith("/api/chat")
            else payload.get("text", {}).get("format", {}).get("schema")
        )
        is_section = not isinstance(schema, dict)
        response_summary = (
            dict(self.bibliography)
            if isinstance(schema, dict)
            and set(schema.get("required", ()))
            == {"title", "authors", "year", "venue"}
            else dict(self.summary)
        )
        if isinstance(schema, dict):
            allowed_fields = set(schema.get("required", ()))
            response_summary = {
                name: value
                for name, value in response_summary.items()
                if name in allowed_fields
            }
        response_text = (
            self.summary.get("summary", SUMMARY["summary"])
            + " 본문 근거와 실험 결과를 충분히 정리합니다."
            if is_section
            else json.dumps(response_summary)
        )
        if url.endswith("/api/chat"):
            return {
                "message": {"content": response_text},
                "prompt_eval_count": 100,
                "eval_count": 20,
            }
        return {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": response_text}
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
    def test_bibliography_title_drops_joined_document_type_label(self):
        self.assertEqual(
            _strip_document_type_title_prefix(
                "Review Article: A precise scaffold study"
            ),
            "A precise scaffold study",
        )

    def test_publication_year_rejects_received_and_cited_years(self):
        first_page = (
            "A Paper Title\nMina Vale\nReceived 3 February 2023; accepted 2024\n"
            "Abstract\nPrior work by Smith et al. (1993) was limited.\n"
            "International Journal of Testing 92 (2025) 15-33"
        )

        self.assertFalse(_publication_year_present("2024", first_page))
        self.assertFalse(_publication_year_present("1993", first_page))
        self.assertTrue(_publication_year_present("2025", first_page))

    def test_unsupported_formal_review_methods_are_removed(self) -> None:
        methods = (
            "systematic review of literature",
            "survey of enzyme systems and microbial hosts",
        )

        self.assertEqual(
            _review_methods_supported_by_source(
                methods,
                "This review surveys enzyme systems and microbial hosts.",
            ),
            ("survey of enzyme systems and microbial hosts",),
        )

    def test_review_nature_is_explicit_for_downstream_qa(self) -> None:
        methods = _ensure_review_nature_method(
            ("Survey of enzyme systems",),
            "This review surveys enzyme systems and microbial hosts. " * 10,
            "source",
        )

        self.assertEqual(
            methods[0],
            "Literature review and synthesis; no new controlled experiment was performed.",
        )
        self.assertEqual(methods[1], "Survey of enzyme systems")

    def test_multiple_documents_are_rejected_before_ai_summary(self) -> None:
        pages = [
            "(19) 대한민국특허청(KR)\n(12) 등록특허공보(B1)\n"
            "(11) 등록번호 10-2052132\n(54) 첫 번째 발명",
            "첫 번째 발명의 본문",
            "(19) 대한민국특허청(KR)\n(12) 등록특허공보(B1)\n"
            "(11) 등록번호 10-1717214\n(54) 두 번째 발명",
        ]

        with self.assertRaisesRegex(SummaryPreparationError, "복수 문서"):
            prepare_text_summary(
                Path("bundle.pdf"),
                pages,
                AppSettings(selected_model="qwen3.5:4b"),
            )

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

    def test_two_invalid_json_responses_use_a_distinct_repair_prompt(self):
        class InvalidTwiceThenValidClient:
            def __init__(self):
                self.calls = []

            def post_json(self, url, headers, payload, timeout_seconds):
                self.calls.append(payload)
                content = (
                    '{"summary": "unfinished"'
                    if len(self.calls) < 3
                    else json.dumps(SUMMARY)
                )
                return {"message": {"content": content}}

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
        client = InvalidTwiceThenValidClient()

        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        self.assertEqual(len(client.calls), 3)
        repair_instructions = client.calls[2]["messages"][0]["content"]
        self.assertIn("FINAL JSON RECOVERY MODE", repair_instructions)
        self.assertIn('"summary":""', repair_instructions)
        self.assertEqual(execution.json_retry_count, 2)
        self.assertTrue(execution.result.prompt_version.endswith("-json-repair"))

    def test_three_invalid_json_responses_report_a_korean_recovery_hint(self):
        class AlwaysInvalidClient:
            def __init__(self):
                self.calls = 0

            def post_json(self, url, headers, payload, timeout_seconds):
                self.calls += 1
                return {"message": {"content": '{"summary": "unfinished"'}}

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

        with self.assertRaisesRegex(ProviderError, "세 번 연속") as raised:
            run_prepared_summary(
                prepared,
                settings,
                MemorySecretStore(),
                http_client=client,
            )

        self.assertEqual(client.calls, 3)
        self.assertEqual(raised.exception.failure_kind, "json_validation")
        self.assertEqual(raised.exception.attempts, 3)

    def test_bibliography_is_extracted_separately_from_first_page(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            [
                "Test Paper Title\n시험 저자\n시험 저널\n2026\n"
                "Introduction\n" + "시험용 본문 근거입니다. " * 40,
            ],
            settings,
        )
        client = FakeHttpClient()
        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            client.calls[0]["payload"]["format"]["required"],
            ["title", "authors", "year", "venue"],
        )
        self.assertNotIn("title", client.calls[1]["payload"]["format"]["properties"])
        self.assertEqual(execution.result.data.title, "Test Paper Title")
        self.assertEqual(execution.result.data.authors, ("시험 저자",))
        self.assertEqual(execution.result.data.year, "2026")
        self.assertEqual(execution.result.data.venue, "시험 저널")
        self.assertEqual(
            execution.bibliography_verified_fields,
            ("title", "authors", "year", "venue"),
        )

    def test_distribution_platform_is_rejected_as_venue(self):
        client = FakeHttpClient(
            bibliography=dict(BIBLIOGRAPHY, venue="ResearchGate")
        )
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            [
                "Downloaded from ResearchGate\nTest Paper Title\n시험 저자\n"
                "시험 저널\n2026\nIntroduction\n" + "시험 본문입니다. " * 50,
            ],
            settings,
        )

        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        self.assertEqual(execution.result.data.venue, "")
        self.assertEqual(execution.bibliography_retry_count, 1)
        self.assertNotIn("venue", execution.bibliography_verified_fields)
        bibliography_prompt = client.calls[0]["payload"]["messages"][0]["content"]
        self.assertIn("ResearchGate", bibliography_prompt)

    def test_small_ollama_model_uses_section_then_synthesis_and_hides_advanced_fields(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:4b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            [
                "Test Paper Title\n시험 저자\n시험 저널\n2026\n"
                "Abstract\n" + "Abstract evidence. " * 40,
                "Introduction\n" + "Question evidence. " * 40,
                "Materials and Methods\n" + "Method evidence. " * 40,
                "Results\nYield increased to 4.5 g/L compared with 1.0 g/L.\n"
                + "Result evidence. " * 40,
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
        self.assertTrue(prepared.regex_fallback.facts)
        self.assertTrue(
            all(
                "[REGEX-VALIDATED CANDIDATES]" not in context
                for context in prepared.section_contexts
            )
        )
        self.assertEqual(
            len(client.calls), len(prepared.section_contexts) + 2
        )
        self.assertIn(
            "plain text only",
            client.calls[1]["payload"]["messages"][0]["content"],
        )
        self.assertIn(
            "final pass",
            client.calls[-1]["payload"]["messages"][0]["content"],
        )
        self.assertIn(
            "[REGEX-VALIDATED CANDIDATES]",
            json.dumps(client.calls[-1]["payload"], ensure_ascii=False),
        )
        for call in client.calls[1:-1]:
            self.assertNotIn("format", call["payload"])
        self.assertGreater(
            len(client.calls[-1]["payload"]["format"]["required"]),
            1,
        )
        self.assertNotIn(
            "contributions",
            client.calls[-1]["payload"]["format"]["properties"],
        )
        self.assertNotIn(
            "limitations",
            client.calls[-1]["payload"]["format"]["properties"],
        )
        self.assertEqual(execution.result.data.contributions, ())
        self.assertEqual(execution.result.data.limitations, ())

    def test_ollama_models_below_8b_keep_hierarchical_basic_analysis(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="custom-model:7b",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            [
                "Introduction\n" + "Scientific introduction evidence. " * 30,
                "Results\n" + "Measured result evidence. " * 30,
            ],
            settings,
        )

        self.assertEqual(prepared.preview.summary_strategy, "hierarchical")

    def test_all_models_omit_figure_and_table_captions(self):
        pages = [
            "Results\n"
            + "Measured scientific evidence. " * 30
            + "\nFigure 3. Detailed microscopy panels and scale bars."
            + "\nTable 2. Full measurement matrix."
        ]
        small = prepare_text_summary(
            Path("paper.paperpack"),
            pages,
            AppSettings(summary_provider="ollama", selected_model="qwen3:4b"),
        )
        large = prepare_text_summary(
            Path("paper.paperpack"),
            pages,
            AppSettings(summary_provider="ollama", selected_model="qwen3:8b"),
        )

        self.assertNotIn("Figure 3", small.document_text)
        self.assertNotIn("Table 2", small.document_text)
        self.assertNotIn("Figure 3", large.document_text)
        self.assertNotIn("Table 2", large.document_text)

    def test_patent_claims_are_copied_without_ai_rewriting(self):
        claims = (
            "CLAIMS\n"
            "1. A bleaching composition comprising enzyme X.\n"
            "2. The composition of claim 1, wherein the pH is 7.0."
        )
        prepared = prepare_text_summary(
            Path("patent.paperpack"),
            [
                "US 2026/0000001 A1\nPatent Application Publication\n"
                "Description\n" + "Technical disclosure. " * 30,
                claims,
                "ABSTRACT OF THE DISCLOSURE\nA short abstract.",
            ],
            AppSettings(summary_provider="ollama", selected_model="qwen3:8b"),
        )

        self.assertEqual(prepared.patent_claims_text, claims)

    def test_patent_page_markers_are_removed_without_touching_claim_numbers(self):
        prepared = prepare_text_summary(
            Path("patent.paperpack"),
            [
                "등록특허 10-1234567\n대한민국특허청\n"
                "발명의 명칭\nA useful invention\n- 1 -\n"
                + "Technical disclosure. " * 30,
                "청구범위\n"
                "1. 제1 구성요소를 포함하는 장치.\n"
                "Page 2 of 3\n"
                "2. 제1항에 있어서, 제2 구성요소를 더 포함하는 장치.\n"
                "3 / 3",
            ],
            AppSettings(summary_provider="ollama", selected_model="qwen3:8b"),
        )

        self.assertNotIn("- 1 -", prepared.document_text)
        self.assertNotIn("Page 2 of 3", prepared.document_text)
        self.assertNotIn("3 / 3", prepared.document_text)
        self.assertEqual(
            prepared.patent_claims_text,
            "청구범위\n"
            "1. 제1 구성요소를 포함하는 장치.\n"
            "2. 제1항에 있어서, 제2 구성요소를 더 포함하는 장치.",
        )

    def test_patent_drawing_section_is_excluded_but_claims_are_preserved(self):
        claims = (
            "청구범위\n"
            "1. 효소 복합체를 포함하는 조성물.\n"
            "2. 제1항에 있어서, 담체를 더 포함하는 조성물."
        )
        prepared = prepare_text_summary(
            Path("patent.paperpack"),
            [
                "등록특허 10-1234567\n대한민국특허청\n"
                "발명의 명칭\n효소 복합체\n"
                + "Technical disclosure. " * 30,
                "도면의 간단한 설명\n"
                "도 1은 효소 반응 장치를 도시한다.\n"
                "도 2는 측정 결과를 도시한다.\n"
                + claims,
            ],
            AppSettings(summary_provider="ollama", selected_model="qwen3:8b"),
        )

        self.assertNotIn("도 1은", prepared.document_text)
        self.assertNotIn("도 2는", prepared.document_text)
        self.assertIn("효소 복합체를 포함", prepared.document_text)
        self.assertEqual(prepared.patent_claims_text, claims)

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
            controller = SummaryController(
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
            controller = SummaryController(
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
                summary_provider="ollama", selected_model="qwen3:8b"
            )
            prepared = prepare_summary(path, settings, SummaryMode.FULL)

        self.assertEqual(prepared.preview.included_pdf_pages, (1, 2, 3, 4))
        self.assertFalse(prepared.preview.sends_to_cloud)
        self.assertFalse(prepared.preview.requires_cloud_consent)

    def test_qwen_4b_on_shared_16gb_memory_uses_safe_8k_context(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:4b",
            resource_profile="balanced",
            hardware_profile={
                "memory_total_gb": 16,
                "memory_available_gb": 5,
                "gpus": [],
            },
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Main academic evidence and methods. " * 4_000],
            settings,
            SummaryMode.FULL,
        )

        self.assertEqual(prepared.preview.context_window, 8_192)
        self.assertTrue(prepared.preview.truncated)
        self.assertLessEqual(
            prepared.preview.estimated_input_tokens,
            8_192 - 3_000,
        )

    def test_qwen_4b_with_dedicated_vram_keeps_24k_context(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:4b",
            resource_profile="balanced",
            hardware_profile={
                "memory_total_gb": 16,
                "memory_available_gb": 5,
                "gpus": [{"vram_total_gb": 12}],
            },
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Main academic evidence and methods. " * 4_000],
            settings,
            SummaryMode.FULL,
        )

        self.assertEqual(prepared.preview.context_window, 24_576)

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
        self.assertEqual(prepared.preview.context_window, 8_192)

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

    def test_preparation_retains_regex_abstract_for_failed_ai_display(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
        )
        abstract = (
            "This abstract reports a controlled enzyme experiment and its measured "
            "result. " * 12
        )

        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            [
                "Test Paper\n2026\n10.1000/example\nAbstract\n"
                + abstract
                + "\nIntroduction\n"
                + "The introduction provides study context. " * 20,
            ],
            settings,
            SummaryMode.FULL,
        )

        self.assertIn("controlled enzyme experiment", prepared.regex_fallback.abstract)
        self.assertNotIn(
            "introduction provides study context",
            prepared.regex_fallback.abstract.casefold(),
        )
        self.assertEqual(prepared.regex_fallback.abstract_pdf_pages, (1,))
        self.assertIn(
            "DOI candidates: 10.1000/example",
            prepared.regex_fallback.facts,
        )
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
        self.assertEqual(execution.result.data.summary, "시험용 요약")
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
        self.assertIn(
            "Choose category from exactly this Korean classification list: "
            "고문서과학",
            instructions,
        )
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
        source_summary = {
            **SUMMARY,
            "summary": "This paper reports the main experimental result.",
            "research_question": "Does the treatment improve the result?",
            "methods": ["The authors performed a controlled experiment."],
            "contributions": ["The treatment improved the measured outcome."],
            "limitations": ["The experiment was performed at laboratory scale."],
            "keywords": ["controlled experiment"],
            "meta_tags": ["laboratory study"],
        }
        client = FakeHttpClient(source_summary)

        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        instructions = client.calls[0]["payload"]["instructions"]
        self.assertIn("paper's original language", instructions)
        self.assertIn("OUTPUT LANGUAGE CONTRACT — ORIGINAL", instructions)
        self.assertIn("Korean is permitted only in category", instructions)
        self.assertEqual(execution.provenance["output_language"], "source")

    def test_source_language_violation_is_retried_once(self):
        english = {
            **SUMMARY,
            "summary": "This paper reports an experimental result.",
            "research_question": "Does the treatment improve the result?",
            "methods": ["The authors performed a controlled experiment."],
            "contributions": ["The treatment improved the measured outcome."],
            "limitations": ["The experiment was performed at laboratory scale."],
            "keywords": ["controlled experiment"],
            "meta_tags": ["laboratory study"],
        }
        korean = {
            **english,
            "summary": "이 논문은 실험 결과를 한국어로 요약했습니다.",
        }

        class LanguageSequenceClient:
            def __init__(self):
                self.calls = []

            def post_json(self, url, headers, payload, timeout_seconds):
                self.calls.append(payload)
                value = korean if len(self.calls) == 1 else english
                return {
                    "message": {"content": json.dumps(value)},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
            summary_language="source",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Introduction\nMain academic evidence. " * 40],
            settings,
            SummaryMode.FULL,
        )
        client = LanguageSequenceClient()

        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(execution.language_retry_count, 1)
        self.assertEqual(execution.provenance["language_retry_count"], 1)
        self.assertIn(
            "previous response violated the OUTPUT LANGUAGE CONTRACT",
            client.calls[1]["messages"][0]["content"],
        )
        self.assertEqual(execution.result.data.summary, english["summary"])

    def test_english_source_retries_han_character_output(self):
        english = {
            **SUMMARY,
            "summary": "This review reports the main evidence synthesis.",
            "research_question": "What evidence supports the conclusion?",
            "methods": ["The review surveys prior literature."],
            "contributions": ["The review integrates the evidence."],
            "limitations": ["The evidence remains limited."],
            "keywords": ["evidence synthesis"],
            "meta_tags": ["review"],
        }
        mixed = {
            **english,
            "summary": "This review reports the main evidence, 但结论不完整.",
        }

        class LanguageSequenceClient:
            def __init__(self):
                self.calls = []

            def post_json(self, url, headers, payload, timeout_seconds):
                self.calls.append(payload)
                value = mixed if len(self.calls) == 1 else english
                return {
                    "message": {"content": json.dumps(value)},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
            summary_language="source",
        )
        prepared = prepare_text_summary(
            Path("review.paperpack"),
            ["Introduction\nMain review evidence. " * 40],
            settings,
            SummaryMode.FULL,
        )
        client = LanguageSequenceClient()

        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(execution.language_retry_count, 1)
        self.assertEqual(execution.result.data.summary, english["summary"])

    def test_repeated_source_language_violation_is_rejected(self):
        korean = {
            **SUMMARY,
            "summary": "이 논문은 영어 원문을 한국어로 번역했습니다.",
            "research_question": "이 결과는 어떤 의미가 있습니까?",
            "methods": ["통제된 실험을 수행했습니다."],
            "contributions": ["측정 결과를 개선했습니다."],
            "limitations": ["실험실 규모의 연구입니다."],
            "keywords": ["통제 실험"],
            "meta_tags": ["실험 연구"],
        }
        client = FakeHttpClient(korean)
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
            summary_language="source",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Introduction\nMain academic evidence. " * 40],
            settings,
            SummaryMode.FULL,
        )

        with self.assertRaisesRegex(
            ProviderError,
            "논문 원문 언어 요약 지시",
        ):
            run_prepared_summary(
                prepared,
                settings,
                MemorySecretStore(),
                http_client=client,
            )

        self.assertEqual(len(client.calls), 2)

    def test_korean_language_violation_is_retried_once(self):
        english = {
            **SUMMARY,
            "summary": "This paper reports an experimental result.",
            "research_question": "Does the treatment improve the result?",
            "methods": ["The authors performed a controlled experiment."],
            "contributions": ["The treatment improved the measured outcome."],
            "limitations": ["The experiment was performed at laboratory scale."],
            "keywords": ["controlled experiment"],
            "meta_tags": ["laboratory study"],
        }
        korean = {
            **SUMMARY,
            "summary": "이 논문은 주요 실험 결과를 한국어로 설명합니다.",
            "research_question": "처리 조건이 측정 결과를 개선합니까?",
            "methods": ["연구진은 통제된 실험을 수행했습니다."],
            "contributions": ["처리 조건이 측정 결과를 개선했습니다."],
            "limitations": ["실험실 규모에서만 검증했습니다."],
            "keywords": ["통제 실험"],
            "meta_tags": ["실험 연구"],
        }

        class LanguageSequenceClient:
            def __init__(self):
                self.calls = []

            def post_json(self, url, headers, payload, timeout_seconds):
                self.calls.append(payload)
                value = english if len(self.calls) == 1 else korean
                return {
                    "message": {"content": json.dumps(value)},
                    "prompt_eval_count": 10,
                    "eval_count": 5,
                }

        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:8b",
            summary_language="ko",
        )
        prepared = prepare_text_summary(
            Path("paper.paperpack"),
            ["Introduction\nMain academic evidence. " * 40],
            settings,
            SummaryMode.FULL,
        )
        client = LanguageSequenceClient()

        execution = run_prepared_summary(
            prepared,
            settings,
            MemorySecretStore(),
            http_client=client,
        )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(execution.language_retry_count, 1)
        self.assertEqual(execution.result.data.summary, korean["summary"])

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
                summary_provider="ollama", selected_model="qwen3:8b"
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
                summary_provider="ollama", selected_model="qwen3:8b"
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

    def test_ollama_model_below_8b_is_excluded_before_ocr(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.pdf"
            document = fitz.open()
            document.new_page()
            document.new_page()
            document.save(path)
            document.close()
            settings = AppSettings(
                summary_provider="ollama", selected_model="qwen3:4b"
            )
            with patch(
                "paper_organizer.application.background_ocr.ocr_page_texts"
            ) as ocr:
                with self.assertRaisesRegex(
                    SummaryPreparationError, "8B 이상 Ollama 모델"
                ):
                    prepare_summary(path, settings)
            ocr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
