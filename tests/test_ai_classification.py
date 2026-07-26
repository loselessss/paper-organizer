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
from paper_organizer.core.paperpack import load_paperpack_metadata
from paper_organizer.core.search_index import search
from paper_organizer.infra.settings import load_settings, save_settings
from paper_organizer.providers.base import (
    SummaryData,
    SummaryRequest,
    SummaryResult,
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


def execution(pdf: Path, **overrides) -> SummaryExecution:
    data = SummaryData(
        summary_ko="AI 요약",
        research_question="질문",
        methods=("방법",),
        contributions=("기여",),
        limitations=("한계",),
        keywords=("키워드",),
        **{
            "title": "Directed Evolution of a Thermostable Enzyme Scaffold",
            "authors": ("A. Researcher", "B. Scientist"),
            "year": "2019",
            "venue": "Journal of Molecular Biology",
            "category": "생명과학",
            "subcategory": "생화학",
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
    )
    result = SummaryResult(
        provider="ollama",
        model="qwen3:4b",
        prompt_version="paper-summary-v2",
        data=data,
    )
    return SummaryExecution(preview, result)


class SystemInstructionTests(unittest.TestCase):
    def test_allowed_categories_are_listed_for_the_model(self):
        request = SummaryRequest(
            document_text="text", allowed_categories=("생물공학", "의학")
        )
        instructions = system_instructions(request)
        self.assertIn("생물공학, 의학", instructions)

    def test_instructions_stay_unchanged_without_a_category_list(self):
        plain = system_instructions(SummaryRequest(document_text="text"))
        self.assertNotIn("Choose category from", plain)


class AiClassificationTests(unittest.TestCase):
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
                "Directed evolution of a thermostable enzyme scaffold",
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
            self.assertEqual(record["analysis"]["summary_ko"], "AI 요약")

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


if __name__ == "__main__":
    unittest.main()
