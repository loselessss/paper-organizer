import os
import tempfile
import time
import unittest
from pathlib import Path

import fitz

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.application.summary_service import (
    SummaryExecution,
    SummaryMode,
    SummaryPreview,
)
from paper_organizer.core.paperpack import load_paperpack_metadata, update_paperpack
from paper_organizer.core.search_index import search
from paper_organizer.infra.settings import load_settings, save_settings
from paper_organizer.providers.base import (
    BibliographyRequest,
    SummaryData,
    SummaryRequest,
    SummaryResult,
    bibliography_instructions,
    system_instructions,
)


PAGES = [
    "Directed evolution of a thermostable enzyme scaffold\n"
    "Journal of Molecular Biology 431 (2019) 1121-1134\n"
    "Abstract We report protein engineering of an enzyme using directed "
    "evolution. doi:10.1016/j.jmb.2019.01.001\n"
    "Introduction Protein engineering tailors enzymes for industrial "
    "biocatalysis. Mutagenesis libraries were screened for protein stability "
    "over many rounds of selection and detailed characterization work.",
    "Methods Recombinant expression in Escherichia coli was followed by "
    "chromatography purification. Catalytic efficiency and kcat were measured "
    "for each enzyme variant from directed evolution.\n"
    "Results The engineered enzyme retained activity.\nReferences follow.",
]


def write_pdf(path: Path, pages: list[str]) -> None:
    pages = [*pages, *(["Test fixture continuation"] * max(0, 3 - len(pages)))]
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(35, 35, 560, 800), text, fontsize=8)
    document.save(path)
    document.close()
    old = time.time() - 120
    os.utime(path, (old, old))


def execution(
    pdf: Path,
    *,
    summary_strategy: str = "direct",
    patent_claims_text: str = "",
    document_type: str = "research_paper",
    document_type_source: str = "auto:regex",
    **overrides,
) -> SummaryExecution:
    data = SummaryData(
        **{
            "summary": "AI 요약",
            "research_question": "질문",
            "methods": ("방법",),
            "contributions": ("기여",),
            "limitations": ("한계",),
            "keywords": ("키워드",),
            "title": "Directed Evolution of a Thermostable Enzyme Scaffold",
            "authors": ("A. Researcher", "B. Scientist"),
            "year": "2019",
            "venue": "Journal of Molecular Biology",
            "category": "생명과학",
            "subcategory": "생화학",
            "meta_tags": ["단백질공학", "효소"],
            "suggested_category": "",
            **overrides,
        },
    )
    preview = SummaryPreview(
        pdf_path=pdf,
        mode=SummaryMode.QUICK,
        provider="ollama",
        model="qwen3:4b",
        page_count=2,
        included_pdf_pages=(1, 2),
        character_count=1000,
        estimated_input_tokens=250,
        truncated=False,
        sends_to_cloud=False,
        requires_cloud_consent=False,
        summary_strategy=summary_strategy,
        document_type=document_type,
        document_type_source=document_type_source,
    )
    result = SummaryResult(
        provider="ollama",
        model="qwen3:4b",
        prompt_version="paper-summary-v2",
        data=data,
    )
    return SummaryExecution(
        preview,
        result,
        patent_claims_text=patent_claims_text,
    )


class SystemInstructionTests(unittest.TestCase):
    def test_allowed_categories_are_listed_for_the_model(self):
        request = SummaryRequest(
            document_text="text", allowed_categories=("생물공학", "의학")
        )
        instructions = system_instructions(request)
        self.assertIn("생물공학, 의학", instructions)
        self.assertIn("suggested_category", instructions)
        self.assertIn("Never add a category", instructions)

    def test_instructions_stay_unchanged_without_a_category_list(self):
        plain = system_instructions(SummaryRequest(document_text="text"))
        self.assertNotIn("Choose category from", plain)
        self.assertNotIn("exact title", plain)
        bibliography = bibliography_instructions(
            BibliographyRequest(document_text="first page")
        )
        self.assertIn("exact title in its original language", bibliography)
        self.assertIn("every byline author", bibliography)

    def test_patent_uses_technical_problem_claim_and_effect_instructions(self):
        instructions = system_instructions(
            SummaryRequest(document_text="patent", is_patent=True)
        )

        self.assertIn("patent, not an academic paper", instructions)
        self.assertIn("technical problem", instructions)
        self.assertIn("claims", instructions)
        self.assertIn("legal conclusion", instructions)

    def test_review_uses_scope_synthesis_conflict_and_gap_instructions(self):
        instructions = system_instructions(
            SummaryRequest(document_text="review", document_type="review_paper")
        )
        self.assertIn("review paper, not a primary research report", instructions)
        self.assertIn("eligibility", instructions)
        self.assertIn("consensus", instructions)
        self.assertIn("evidence gaps", instructions)

    def test_review_section_preserves_selection_conflicts_and_evidence_strength(self):
        instructions = system_instructions(
            SummaryRequest(
                document_text="review section",
                document_type="review_paper",
                stage="section",
            )
        )

        self.assertIn("review-paper section", instructions)
        self.assertIn("named systems, organisms or populations", instructions)
        self.assertIn("process order and dependencies", instructions)
        self.assertIn("Never call the review systematic", instructions)
        self.assertIn("at most 150 words", instructions)

    def test_review_synthesis_maps_review_evidence_to_output_fields(self):
        instructions = system_instructions(
            SummaryRequest(
                document_text="section summaries",
                document_type="review_paper",
                stage="synthesis",
                advanced_analysis=False,
            )
        )

        self.assertIn("final pass over evidence summaries from a review paper", instructions)
        self.assertIn("Put the objective and scope in research_question", instructions)
        self.assertIn("without calling the review systematic", instructions)
        self.assertIn("at least three evidence-dense sentences", instructions)
        self.assertIn("synergistic or integrated relationships", instructions)
        self.assertIn("rather than a new controlled experiment", instructions)
        self.assertIn("evidence gaps in summary", instructions)

    def test_research_section_preserves_complete_numeric_comparisons(self):
        instructions = system_instructions(
            SummaryRequest(
                document_text="results section",
                document_type="research_paper",
                stage="section",
            )
        )

        self.assertIn("complete result comparisons", instructions)
        self.assertIn("exact numeric values and units", instructions)
        self.assertIn("at most 160 words", instructions)
        self.assertIn("copyright or license text", instructions)
        self.assertIn("primary research paper", instructions)

    def test_research_synthesis_prioritizes_endpoints_over_background(self):
        instructions = system_instructions(
            SummaryRequest(
                document_text="section summaries",
                document_type="research_paper",
                stage="synthesis",
                advanced_analysis=False,
            )
        )

        self.assertIn("tested question in research_question", instructions)
        self.assertIn("Limit background to at most one sentence", instructions)
        self.assertIn("at least three evidence-dense result sentences", instructions)
        self.assertIn("every distinct primary endpoint", instructions)
        self.assertIn("experimental limitations in summary", instructions)

    def test_abstract_only_prompt_forbids_body_inference(self):
        instructions = system_instructions(
            SummaryRequest(
                document_text="abstract text",
                document_type="research_paper",
                stage="abstract",
                advanced_analysis=False,
            )
        )

        self.assertIn("only the paper's own Abstract", instructions)
        self.assertIn("one or two concise paragraphs", instructions)
        self.assertIn("Put the rewritten Abstract only in summary", instructions)
        self.assertNotIn("essential experimental design", instructions)


class AiClassificationTests(unittest.TestCase):
    def test_short_journal_masthead_does_not_replace_deterministic_title(self):
        record = {
            "bibliography": {"title": "Old Automatic Title"},
            "classification": {},
            "curation": {
                "field_sources": {"bibliography.title": "auto:regex"},
                "locked_fields": [],
            },
        }

        LibraryWorkflowController._apply_ai_bibliography(
            record,
            execution(
                Path("paper.pdf"),
                title="Scientific Reports",
                venue="Scientific Reports",
                authors=("Human cellular aging is usually marked by senescence.",),
            ).result.data,
            "ai:ollama",
            preferred_title="Quantifying circulating cell-free DNA in humans",
            preferred_authors=["Romain Meddeb", "Zahra Al Amir Dache"],
        )

        self.assertEqual(
            record["bibliography"]["title"],
            "Quantifying circulating cell-free DNA in humans",
        )
        self.assertEqual(
            record["curation"]["field_sources"]["bibliography.title"],
            "auto:regex",
        )
        self.assertEqual(
            record["bibliography"]["authors"],
            ["Romain Meddeb", "Zahra Al Amir Dache"],
        )
        self.assertEqual(
            record["curation"]["field_sources"]["bibliography.authors"],
            "auto:regex",
        )

    def test_ai_bibliography_rejects_section_or_repeated_header_as_title(self):
        record = {
            "bibliography": {"title": "Existing Candidate"},
            "classification": {},
            "curation": {
                "field_sources": {"bibliography.title": "auto:regex"},
                "locked_fields": [],
            },
        }

        LibraryWorkflowController._apply_ai_bibliography(
            record,
            execution(Path("paper.pdf"), title="JOURNAL RUNNING HEADER").result.data,
            "ai:ollama",
            excluded_titles={"journal running header"},
        )

        self.assertEqual(record["bibliography"]["title"], "Existing Candidate")

    def test_ai_confirmed_document_type_source_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, library, pack = self._organized(root)
            pdf = controller.materialize_pdf(pack)

            controller.apply_analysis_result(
                pack,
                execution(
                    pdf,
                    document_type="review_paper",
                    document_type_source="ai:ollama",
                ),
            )

            final = next((library / "papers").rglob("*.paperpack"))
            record = load_paperpack_metadata(final)
            self.assertEqual(record["document"]["type"], "review_paper")
            self.assertEqual(
                record["curation"]["field_sources"]["document.type"],
                "ai:ollama",
            )

    def test_patent_ignores_ai_journal_venue(self):
        record = {
            "detection": {"document_type": "patent"},
            "bibliography": {},
            "classification": {},
            "curation": {"field_sources": {}, "locked_fields": []},
        }

        LibraryWorkflowController._apply_ai_bibliography(
            record,
            execution(Path("patent.pdf")).result.data,
            "ai:ollama",
        )

        self.assertEqual(record["bibliography"]["venue"], "")

    def test_patent_claims_are_saved_verbatim_in_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, _library, pack = self._organized(root)
            record = load_paperpack_metadata(pack)
            record.setdefault("document", {})["type"] = "patent"
            record.setdefault("detection", {})["document_type"] = "patent"
            update_paperpack(pack, record, changed_by="test")
            pdf = controller.materialize_pdf(pack)
            claims = "CLAIMS\n1. A composition comprising X.\n2. The composition of claim 1."

            controller.apply_analysis_result(
                pack,
                execution(pdf, patent_claims_text=claims),
            )

            moved = next(pack.parent.parent.parent.rglob("*.paperpack"))
            saved = load_paperpack_metadata(moved)
            self.assertEqual(saved["analysis"]["patent_claims"], claims)

    def _organized(self, root: Path):
        input_dir = root / "downloads"
        library = root / "library"
        input_dir.mkdir()
        settings_path = root / "settings.json"
        controller = LibraryWorkflowController(settings_path)
        controller.save_paths(input_dir, library, auto_enabled=False)
        settings = load_settings(settings_path)
        settings.minimum_age_seconds = 0
        save_settings(settings, settings_path)
        write_pdf(input_dir / "paper.pdf", PAGES)
        controller.scan()
        controller.scan()
        pack = next((library / "papers").rglob("*.paperpack"))
        return controller, library, pack

    def test_ai_overwrites_regex_classification_and_moves_the_paperpack(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, library, pack = self._organized(root)
            self.assertEqual(pack.parent.parent.name, "생물공학")
            pdf = controller.materialize_pdf(pack)

            controller.apply_analysis_result(pack, execution(pdf))

            self.assertFalse(pack.exists())
            moved = next((library / "papers").rglob("*.paperpack"))
            self.assertEqual(moved.parent.parent.name, "생명과학")
            self.assertEqual(moved.parent.name, "생화학")
            record = load_paperpack_metadata(moved)
            self.assertEqual(record["classification"]["category"], "생명과학")
            self.assertEqual(
                record["curation"]["field_sources"]["classification.category"],
                "ai:ollama",
            )
            self.assertEqual(
                record["bibliography"]["title"],
                "Directed Evolution of a Thermostable Enzyme Scaffold",
            )
            self.assertEqual(record["bibliography"]["year"], 2019)
            self.assertEqual(record["file"]["relative_path"], moved.relative_to(library).as_posix())
            self.assertEqual(len(search(library, "halophilic")), 0)
            self.assertEqual(len(search(library, "chromatography")), 1)

    def test_user_edited_fields_are_never_overwritten_by_ai(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, library, pack = self._organized(root)
            entry = controller.list_library()[0]
            edited = entry.metadata
            edited.title = "사람이 고친 제목"
            edited.category = "화학"
            edited.subcategory = "유기화학"
            controller.update_library_metadata(entry, edited)
            moved = next((library / "papers").rglob("*.paperpack"))
            pdf = controller.materialize_pdf(moved)

            controller.apply_analysis_result(moved, execution(pdf))

            final = next((library / "papers").rglob("*.paperpack"))
            record = load_paperpack_metadata(final)
            self.assertEqual(record["bibliography"]["title"], "사람이 고친 제목")
            self.assertEqual(record["classification"]["category"], "화학")
            self.assertEqual(final.parent.parent.name, "화학")
            self.assertEqual(record["analysis"]["summary"], "AI 요약")

    def test_paperpack_stays_when_ai_returns_no_category(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, library, pack = self._organized(root)
            pdf = controller.materialize_pdf(pack)

            controller.apply_analysis_result(
                pack, execution(pdf, category="", subcategory="")
            )

            self.assertTrue(pack.is_file())
            record = load_paperpack_metadata(pack)
            self.assertEqual(record["classification"]["category"], "생물공학")

    def test_reanalysis_replaces_ai_fields_but_preserves_user_tags(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, library, pack = self._organized(root)
            record = load_paperpack_metadata(pack)
            record.setdefault("classification", {})["tags"] = ["내 태그"]
            record.setdefault("curation", {}).setdefault("field_sources", {})[
                "classification.tags"
            ] = "user"
            update_paperpack(pack, record, changed_by="user")
            pdf = controller.materialize_pdf(pack)

            controller.apply_analysis_result(
                pack,
                execution(
                    pdf,
                    summary="첫 요약",
                    meta_tags=("효소공학", "바이오촉매", "효소공학"),
                ),
            )
            moved = next((library / "papers").rglob("*.paperpack"))
            moved_pdf = controller.materialize_pdf(moved)
            controller.apply_analysis_result(
                moved,
                execution(
                    moved_pdf,
                    summary="새 요약",
                    meta_tags=("열안정성", "단백질 설계"),
                ),
            )

            final = next((library / "papers").rglob("*.paperpack"))
            record = load_paperpack_metadata(final)
            self.assertEqual(record["description"]["summary"], "새 요약")
            self.assertEqual(record["classification"]["tags"], ["내 태그"])
            self.assertEqual(
                record["classification"]["ai_tags"],
                ["열안정성", "단백질 설계"],
            )

    def test_small_model_reanalysis_preserves_existing_advanced_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, library, pack = self._organized(root)
            pdf = controller.materialize_pdf(pack)
            controller.apply_analysis_result(
                pack,
                execution(
                    pdf,
                    contributions=("8B 기여",),
                    limitations=("8B 한계",),
                ),
            )
            moved = next((library / "papers").rglob("*.paperpack"))
            moved_pdf = controller.materialize_pdf(moved)
            controller.apply_analysis_result(
                moved,
                execution(
                    moved_pdf,
                    summary_strategy="hierarchical",
                    contributions=(),
                    limitations=(),
                ),
            )
            final = next((library / "papers").rglob("*.paperpack"))
            description = load_paperpack_metadata(final)["description"]
            self.assertEqual(description["contributions"], ["8B 기여"])
            self.assertEqual(description["limitations"], ["8B 한계"])

    def test_bibliography_only_reanalysis_does_not_erase_existing_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, library, pack = self._organized(root)
            pdf = controller.materialize_pdf(pack)
            controller.apply_analysis_result(
                pack,
                execution(pdf, summary="기존 AI 요약"),
            )
            moved = next((library / "papers").rglob("*.paperpack"))
            moved_pdf = controller.materialize_pdf(moved)
            controller.apply_analysis_result(
                moved,
                execution(
                    moved_pdf,
                    summary="",
                    summary_strategy="bibliography_only",
                ),
            )

            final = next((library / "papers").rglob("*.paperpack"))
            record = load_paperpack_metadata(final)
            self.assertEqual(record["description"]["summary"], "기존 AI 요약")
            self.assertEqual(record["analysis"]["summary"], "")

    def test_category_suggestion_requires_approval_then_can_be_requeued(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, library, pack = self._organized(root)
            pdf = controller.materialize_pdf(pack)
            controller.apply_analysis_result(
                pack,
                execution(
                    pdf,
                    category="",
                    subcategory="",
                    suggested_category="우주생물학",
                ),
            )
            entry = controller.list_library()[0]
            settings = load_settings(root / "settings.json")
            self.assertNotIn("우주생물학", settings.research_categories)
            self.assertEqual(
                entry.record["analysis"]["suggested_category"], "우주생물학"
            )

            approved = controller.approve_category_suggestion(entry)
            queued, problems = controller.queue_reanalysis([entry], high=True)

            self.assertEqual(approved, "우주생물학")
            self.assertIn(
                "우주생물학",
                load_settings(root / "settings.json").research_categories,
            )
            self.assertEqual((queued, problems), (1, ()))
            item = next(
                value
                for value in controller.analysis_queue()
                if value.file_sha256 == entry.record["file"]["sha256"]
            )
            self.assertEqual(item.status, "organized_pending_analysis")
            self.assertEqual(item.priority, 1)
            self.assertEqual(
                load_paperpack_metadata(entry.sidecar_path)["analysis"]["summary"],
                "AI 요약",
            )


if __name__ == "__main__":
    unittest.main()
