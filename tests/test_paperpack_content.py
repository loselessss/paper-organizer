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
from paper_organizer.core.paperpack import (
    build_content_payload,
    content_pages,
    create_paperpack,
    load_paperpack_content,
    update_paperpack,
)
from paper_organizer.infra.settings import load_settings, save_settings


PAGES = [
    "Directed evolution of a thermostable enzyme\n"
    "Abstract We report protein engineering results. Introduction follows.",
    "Methods Recombinant expression in Escherichia coli.\n"
    "Results The enzyme retained activity. References and doi listed below.",
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


class ContentPayloadTests(unittest.TestCase):
    def test_payload_keeps_one_entry_per_page(self):
        payload = build_content_payload(["첫 페이지", "second page"])
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["page_count"], 2)
        self.assertEqual(payload["extractor"], "pymupdf")
        self.assertEqual(
            [(entry["page"], entry["text"]) for entry in payload["pages"]],
            [(1, "첫 페이지"), (2, "second page")],
        )

    def test_content_pages_skips_blank_and_malformed_entries(self):
        payload = {
            "pages": [
                {"page": 1, "text": "본문"},
                {"page": 2, "text": "   "},
                "not-an-object",
                {"page": 3, "text": "tail"},
            ]
        }
        self.assertEqual(content_pages(payload), [(1, "본문"), (3, "tail")])
        self.assertEqual(content_pages(None), [])


class OrganizeContentTests(unittest.TestCase):
    def _controller(self, root: Path):
        input_dir = root / "downloads"
        library = root / "library"
        input_dir.mkdir()
        settings_path = root / "settings.json"
        controller = LibraryWorkflowController(settings_path)
        controller.save_paths(input_dir, library, auto_enabled=False)
        settings = load_settings(settings_path)
        settings.minimum_age_seconds = 0
        save_settings(settings, settings_path)
        return controller, input_dir, library

    def test_organize_stores_page_text_for_search(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root)
            write_pdf(input_dir / "paper.pdf", PAGES)
            controller.scan()
            item = controller.scan().items[0]
            organized = controller.organize(
                item, EditablePaperMetadata(title="Stored content")
            )
            pages = content_pages(load_paperpack_content(organized.pdf_path))
            self.assertEqual(len(pages), 3)
            self.assertIn("thermostable", pages[0][1])
            self.assertIn("Escherichia", pages[1][1])

    def test_backfill_fills_empty_content_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "paper.pdf", PAGES)
            controller.scan()
            item = controller.scan().items[0]
            organized = controller.organize(
                item, EditablePaperMetadata(title="Backfill target")
            )
            from paper_organizer.core.paperpack import load_paperpack_metadata

            update_paperpack(
                organized.pdf_path,
                load_paperpack_metadata(organized.pdf_path),
                content={},
                changed_by="test",
            )
            self.assertEqual(
                content_pages(load_paperpack_content(organized.pdf_path)), []
            )

            filled, problems = controller.backfill_content()
            self.assertEqual((filled, problems), (1, ()))
            self.assertEqual(
                len(content_pages(load_paperpack_content(organized.pdf_path))), 3
            )

            again, problems = controller.backfill_content()
            self.assertEqual((again, problems), (0, ()))

    def test_legacy_created_paperpack_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "legacy.pdf"
            write_pdf(pdf, PAGES)
            record = {
                "identity": {"work_id": "content:test", "file_id": "sha256:test"},
                "file": {"relative_path": "papers/legacy.paperpack"},
            }
            destination = root / "legacy.paperpack"
            create_paperpack(
                destination,
                pdf,
                record,
                content=build_content_payload(["기존 본문"]),
            )
            self.assertEqual(
                content_pages(load_paperpack_content(destination)), [(1, "기존 본문")]
            )


if __name__ == "__main__":
    unittest.main()
