import os
import tempfile
import time
import unittest
from pathlib import Path

import fitz

from paper_organizer.application.library_workflow import (
    EditablePaperMetadata,
    LibraryWorkflowController,
)
from paper_organizer.core.paperpack import load_paperpack_metadata
from paper_organizer.infra.settings import load_settings, save_settings


def protein_pages() -> list[str]:
    return [
        "Directed evolution of a thermostable enzyme scaffold\n"
        "Journal of Molecular Biology 431 (2019) 1121-1134\n"
        "Abstract We report protein engineering of an enzyme using directed "
        "evolution and rational design. doi:10.1016/j.jmb.2019.01.001\n"
        "Introduction Protein engineering has become central to biotechnology "
        "because enzymes can be tailored for industrial biocatalysis. "
        "Mutagenesis libraries were screened for protein stability across many "
        "rounds of selection and characterization.",
        "Methods Recombinant protein expression in Escherichia coli was followed "
        "by chromatography purification. Catalytic efficiency and kcat were "
        "measured for each enzyme variant obtained by directed evolution.\n"
        "Results The engineered enzyme retained activity at elevated temperature.\n"
        "References 1. Smith et al. protein engineering review.",
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


def patent_pages() -> list[str]:
    return [
        "US Patent Application\nPublication Number US20260000001\n"
        "Inventor: Example Inventor\nApplicant: Example Labs\n"
        + "A system and method for organizing technical documents. " * 20,
        "Detailed Description\n" + "The disclosed system processes documents. " * 25,
        "Claims\n1. A method comprising receiving and classifying a document. " * 20,
    ]


class AutoOrganizeTests(unittest.TestCase):
    def test_patent_is_included_in_automatic_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "patent.pdf", patent_pages())

            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.auto_organized), 1)
            pack = next((library / "papers").rglob("*.paperpack"))
            record = load_paperpack_metadata(pack)
            self.assertTrue(record["detection"]["is_patent"])
            self.assertEqual(record["detection"]["document_type"], "patent")
    def _controller(self, root: Path, *, auto_organize: bool = True):
        input_dir = root / "downloads"
        library = root / "library"
        input_dir.mkdir()
        settings_path = root / "settings.json"
        controller = LibraryWorkflowController(settings_path)
        controller.save_paths(
            input_dir,
            library,
            auto_enabled=False,
            auto_organize_academic=auto_organize,
        )
        settings = load_settings(settings_path)
        settings.minimum_age_seconds = 0
        save_settings(settings, settings_path)
        return controller, input_dir, library

    def test_academic_paper_is_stored_and_queued_without_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "paper.pdf", protein_pages())

            controller.scan()
            result = controller.scan()

            self.assertEqual(result.items, ())
            self.assertEqual(len(result.auto_organized), 1)
            packs = list((library / "papers").rglob("*.paperpack"))
            self.assertEqual(len(packs), 1)
            self.assertEqual(packs[0].parent.parent.name, "생물공학")
            self.assertEqual(packs[0].parent.name, "단백질공학")

            queue = controller.analysis_queue()
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0].status, "organized_pending_analysis")

            record = load_paperpack_metadata(packs[0])
            sources = record["curation"]["field_sources"]
            self.assertEqual(sources["classification.category"], "auto:regex")
            self.assertEqual(record["bibliography"]["venue"], "Journal of Molecular Biology")

    def test_auto_organize_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root, auto_organize=False)
            write_pdf(input_dir / "paper.pdf", protein_pages())

            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.items), 1)
            self.assertEqual(result.auto_organized, ())
            self.assertEqual(list((library / "papers").rglob("*.paperpack")), [])
            self.assertEqual(controller.analysis_queue()[0].status, "pending_review")

    def test_duplicate_candidates_stay_for_human_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "first.pdf", protein_pages())
            controller.scan()
            controller.scan()
            self.assertEqual(len(list((library / "papers").rglob("*.paperpack"))), 1)

            write_pdf(input_dir / "second.pdf", protein_pages())
            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.items), 1)
            self.assertEqual(result.auto_organized, ())
            self.assertIsNotNone(result.items[0].duplicate)
            self.assertEqual(len(list((library / "papers").rglob("*.paperpack"))), 1)

    def test_non_academic_pdf_stays_for_human_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "notes.pdf", ["짧은 메모입니다."])

            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.items), 1)
            self.assertNotEqual(result.items[0].detection_status, "academic_likely")
            self.assertEqual(result.auto_organized, ())
            self.assertEqual(list((library / "papers").rglob("*.paperpack")), [])

    def test_focus_categories_limit_auto_classification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            settings_path = root / "settings.json"
            settings = load_settings(settings_path)
            settings.focus_categories = ["의학"]
            save_settings(settings, settings_path)
            write_pdf(input_dir / "paper.pdf", protein_pages())

            controller.scan()
            controller.scan()

            packs = list((library / "papers").rglob("*.paperpack"))
            self.assertEqual(len(packs), 1)
            self.assertEqual(packs[0].parent.parent.name, "Uncategorized")

    def test_manual_organize_still_records_user_as_field_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root, auto_organize=False)
            write_pdf(input_dir / "paper.pdf", protein_pages())
            controller.scan()
            item = controller.scan().items[0]

            organized = controller.organize(
                item, EditablePaperMetadata(title="Curated", category="생물공학")
            )

            record = load_paperpack_metadata(organized.pdf_path)
            self.assertEqual(
                record["curation"]["field_sources"]["classification.category"], "user"
            )

    def test_suggest_metadata_fills_category_and_venue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root, auto_organize=False)
            write_pdf(input_dir / "paper.pdf", protein_pages())
            controller.scan()
            item = controller.scan().items[0]

            suggested = controller.suggest_metadata(item)

            self.assertEqual(suggested.category, "생물공학")
            self.assertEqual(suggested.subcategory, "단백질공학")
            self.assertEqual(suggested.venue, "Journal of Molecular Biology")


if __name__ == "__main__":
    unittest.main()
