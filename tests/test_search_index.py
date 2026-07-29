import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import fitz

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.core.search_index import (
    fts5_available,
    indexed_file_ids,
    rebuild_search_index,
    search,
    search_index_path,
    search_metadata,
)
from paper_organizer.core.paperpack import (
    iter_paperpacks,
    load_paperpack_metadata,
    update_paperpack,
)
from paper_organizer.infra.settings import load_settings, save_settings


PROTEIN_PAGES = [
    "Directed evolution of a thermostable enzyme scaffold\n"
    "Journal of Molecular Biology 431 (2019) 1121-1134\n"
    "Abstract We report protein engineering using directed evolution. "
    "doi:10.1016/j.jmb.2019.01.001\n"
    "Introduction Protein engineering tailors enzymes for industrial "
    "biocatalysis. Mutagenesis libraries were screened for protein stability.",
    "Methods Recombinant expression in Escherichia coli was followed by "
    "chromatography purification. Catalytic efficiency and kcat were measured.\n"
    "Results Halophilic variants retained activity in brine.\n"
    "References follow.",
]

VISION_PAGES = [
    "A transformer architecture for image classification\n"
    "IEEE Transactions on Pattern Analysis 44 (2022) 233-245\n"
    "Abstract We train a deep learning model on a large annotated dataset and "
    "report benchmark accuracy against convolutional baselines. "
    "doi:10.1109/tpami.2022.000001\n"
    "Introduction Neural networks dominate computer vision benchmarks because "
    "large scale pretraining transfers well to downstream tasks such as image "
    "classification, object detection and semantic segmentation. This work "
    "revisits the design space of attention blocks and reports how patch size "
    "and depth interact with the available training budget.",
    "Methods The neural network was trained on eight GPUs with fine-tuning on "
    "each downstream benchmark. We follow standard augmentation and report the "
    "mean of three runs for every configuration.\n"
    "Results Object detection accuracy improved over the convolutional baseline "
    "while inference cost stayed comparable at the same resolution.\n"
    "References follow in the appendix.",
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


@unittest.skipUnless(fts5_available(), "SQLite FTS5 is unavailable")
class SearchIndexTests(unittest.TestCase):
    def _library(self, root: Path) -> tuple[LibraryWorkflowController, Path]:
        input_dir = root / "downloads"
        library = root / "library"
        input_dir.mkdir()
        settings_path = root / "settings.json"
        controller = LibraryWorkflowController(settings_path)
        controller.save_paths(input_dir, library, auto_enabled=False)
        settings = load_settings(settings_path)
        settings.minimum_age_seconds = 0
        save_settings(settings, settings_path)
        write_pdf(input_dir / "protein.pdf", PROTEIN_PAGES)
        write_pdf(input_dir / "vision.pdf", VISION_PAGES)
        controller.scan()
        controller.scan()
        return controller, library

    def test_stale_summary_column_is_rebuilt_as_disposable_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = search_index_path(root)
            target.parent.mkdir(parents=True)
            connection = sqlite3.connect(target)
            connection.execute(
                "CREATE TABLE works (file_id TEXT PRIMARY KEY, summary_ko TEXT)"
            )
            connection.execute(
                "CREATE VIRTUAL TABLE pages USING fts5(file_id, page, text)"
            )
            connection.commit()
            connection.close()

            indexed, problems = rebuild_search_index(root)

            self.assertEqual(indexed, 0)
            self.assertEqual(problems, ())
            connection = sqlite3.connect(target)
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(works)")
            }
            connection.close()
            self.assertIn("summary", columns)
            self.assertIn("patent_number", columns)
            self.assertNotIn("summary_ko", columns)

    def test_body_text_is_searchable_after_auto_organize(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _controller, library = self._library(root)

            self.assertTrue(search_index_path(library).is_file())
            self.assertEqual(len(indexed_file_ids(library)), 2)

            hits = search(library, "halophilic")
            self.assertEqual(len(hits), 1)
            self.assertIn("thermostable", hits[0].title.casefold())
            self.assertEqual(hits[0].page, 2)
            self.assertIn("[Halophilic]", hits[0].snippet)

    def test_search_matches_all_terms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _controller, library = self._library(root)

            self.assertEqual(len(search(library, "neural")), 1)
            self.assertEqual(len(search(library, "abstract")), 2)
            self.assertEqual(search(library, "halophilic neural"), [])

    def test_rebuild_is_repeatable_and_recreates_a_deleted_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _controller, library = self._library(root)
            search_index_path(library).unlink()
            self.assertEqual(search(library, "halophilic"), [])

            indexed, problems = rebuild_search_index(library)

            self.assertEqual((indexed, problems), (2, ()))
            self.assertEqual(len(search(library, "halophilic")), 1)

            again, problems = rebuild_search_index(library)
            self.assertEqual((again, problems), (2, ()))
            self.assertEqual(len(indexed_file_ids(library)), 2)

    def test_metadata_fallback_search_uses_stored_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _controller, library = self._library(root)

            hits = search_metadata(library, "Journal of Molecular Biology")

            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0].venue, "Journal of Molecular Biology")

    def test_patent_index_searches_registration_and_application_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _controller, library = self._library(root)
            paperpack = next(iter(iter_paperpacks(library)))
            record = load_paperpack_metadata(paperpack)
            record.setdefault("document", {})["type"] = "patent"
            record["patent"] = {
                "office": "KIPO",
                "publication_number": "10-2052132",
                "application_number": "10-2017-0092335",
                "assignee": "고려대학교 산학협력단",
            }
            update_paperpack(paperpack, record, changed_by="test")
            rebuild_search_index(library)

            self.assertEqual(len(search_metadata(library, "10-2052132")), 1)
            self.assertEqual(len(search_metadata(library, "10-2017-0092335")), 1)

    def test_controller_search_returns_library_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, _library = self._library(root)

            entries = controller.search_library("halophilic")

            self.assertEqual(len(entries), 1)
            self.assertIn("thermostable", entries[0].metadata.title.casefold())

            self.assertEqual(len(controller.search_library("")), 2)


if __name__ == "__main__":
    unittest.main()
