import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import fitz

from paper_organizer.application.library_workflow import (
    EditablePaperMetadata,
    LibraryWorkflowController,
    default_input_dir,
)
from paper_organizer.infra.settings import load_settings, save_settings
from paper_organizer.models.paper import DuplicateKind
from paper_organizer.core.paperpack import (
    PAPERPACK_SUFFIX,
    inspect_paperpack,
    load_paperpack_metadata,
)


def academic_pages() -> list[str]:
    return [
        "Example Paper for Cell Analysis\nA. Researcher\nAbstract\n" +
        "This study presents a reliable scientific method for cell analysis. " * 14,
        "Introduction\n" +
        "The experiment uses cultured cells under controlled conditions. " * 18,
        "Methods\n" +
        "A549 cells were cultured in DMEM with 10 percent FBS. " * 18,
        "Results\n" +
        "The proposed method improved the measured outcome in every cohort. " * 18,
        "Conclusion\n" +
        "The method improves accuracy and robustness. " * 14 + "\nReferences\n[1] Example",
    ]


def write_pdf(path: Path, pages: list[str]) -> None:
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(35, 35, 560, 800), text, fontsize=8)
    document.save(path)
    document.close()
    old = time.time() - 120
    os.utime(path, (old, old))


class LibraryWorkflowTests(unittest.TestCase):
    def _controller(
        self, root: Path, *, sync: bool = False, remove_source: bool = False
    ):
        input_dir = root / "downloads"
        library = root / "library"
        input_dir.mkdir()
        settings_path = root / "settings.json"
        controller = LibraryWorkflowController(settings_path)
        controller.save_paths(
            input_dir,
            library,
            auto_enabled=False,
            metadata_sync_dir=(root / "OneDrive" / "Paper JSON") if sync else None,
            remove_source_after_import=remove_source,
        )
        settings = load_settings(settings_path)
        settings.minimum_age_seconds = 0
        save_settings(settings, settings_path)
        return controller, input_dir, library

    def _scan_twice(self, controller: LibraryWorkflowController):
        first = controller.scan()
        self.assertEqual(first.items, ())
        return controller.scan()

    def test_downloads_is_the_default_input_folder(self):
        self.assertEqual(default_input_dir(), Path.home() / "Downloads")

    def test_organize_writes_paperpack_index_and_onedrive_json_mirror(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root, sync=True)
            source = input_dir / "paper.pdf"
            write_pdf(source, academic_pages())
            result = self._scan_twice(controller)
            self.assertEqual(len(result.items), 1)
            self.assertEqual(controller.analysis_queue()[0].status, "pending_review")
            metadata = EditablePaperMetadata(
                title="Curated Paper",
                authors=["A. Researcher"],
                year=2026,
                venue="Nature Methods",
                category="Life Science",
                subcategory="Cell Biology",
                tags=["DMEM", "A549"],
                summary_ko="사용자가 검토한 설명",
            )
            organized = controller.organize(result.items[0], metadata)
            self.assertTrue(organized.pdf_path.is_file())
            self.assertTrue(organized.sidecar_path.is_file())
            self.assertEqual(organized.pdf_path.suffix, PAPERPACK_SUFFIX)
            self.assertEqual(organized.pdf_path, organized.sidecar_path)
            self.assertTrue(source.exists())
            self.assertEqual(controller.scan().items, ())
            self.assertEqual(
                controller.analysis_queue()[0].status, "organized_pending_analysis"
            )
            index = json.loads((library / "index" / "library.json").read_text(encoding="utf-8"))
            self.assertEqual(index["work_count"], 1)
            self.assertEqual(index["works"][0]["venue"], "Nature Methods")
            self.assertEqual(len(controller.list_library("nature methods")), 1)
            mirrored = list(
                (root / "OneDrive" / "Paper JSON" / "backup" / "paperpacks").rglob(
                    "*.metadata.json"
                )
            )
            self.assertEqual(len(mirrored), 1)
            self.assertTrue(
                (root / "OneDrive" / "Paper JSON" / "portable-library.json").is_file()
            )
            self.assertTrue(
                (
                    root
                    / "OneDrive"
                    / "Paper JSON"
                    / "backup"
                    / "state"
                    / "analysis-queue.json"
                ).is_file()
            )
            manifest = json.loads(
                (root / "OneDrive" / "Paper JSON" / "sync-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["mode"], "original-backup-plus-portable-sync")

    def test_library_metadata_is_editable_with_history_and_reindex(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "paper.pdf", academic_pages())
            item = self._scan_twice(controller).items[0]
            controller.organize(item, EditablePaperMetadata(title="Before"))
            entry = controller.list_library()[0]
            updated = controller.update_library_metadata(
                entry,
                EditablePaperMetadata(
                    title="After",
                    authors=["Curator"],
                    year=2025,
                    venue="Cell",
                    category="Edited",
                    subcategory="Index",
                    tags=["medium", "DMEM"],
                    summary_ko="수정된 설명",
                ),
            )
            self.assertEqual(updated.metadata.title, "After")
            self.assertEqual(updated.metadata.venue, "Cell")
            self.assertEqual(controller.list_library("dmem")[0].metadata.title, "After")
            self.assertEqual(inspect_paperpack(updated.sidecar_path).revision, 2)
            saved = load_paperpack_metadata(updated.sidecar_path)
            self.assertEqual(saved["curation"]["revision"], 2)
            self.assertEqual(saved["curation"]["last_edited_by"], "user")

    def test_organize_can_remove_input_pdf_after_verified_pack_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(
                root, remove_source=True
            )
            source = input_dir / "paper.pdf"
            write_pdf(source, academic_pages())
            item = self._scan_twice(controller).items[0]
            organized = controller.organize(
                item, EditablePaperMetadata(title="Moved into paperpack")
            )
            self.assertFalse(source.exists())
            self.assertTrue(organized.pdf_path.is_file())

    def test_materialize_pdf_extracts_verified_cache_for_viewer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root)
            source = input_dir / "paper.pdf"
            write_pdf(source, academic_pages())
            item = self._scan_twice(controller).items[0]
            organized = controller.organize(
                item, EditablePaperMetadata(title="Viewer Copy")
            )
            opened = controller.materialize_pdf(organized.pdf_path)
            self.assertEqual(opened.suffix, ".pdf")
            self.assertTrue(opened.is_file())
            self.assertEqual(opened.read_bytes(), source.read_bytes())

    def test_researchgate_variant_can_only_be_moved_to_recoverable_trash_after_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root)
            write_pdf(input_dir / "publisher.pdf", academic_pages())
            original = self._scan_twice(controller).items[0]
            controller.organize(original, EditablePaperMetadata(title="Published Paper"))
            cover = (
                "ResearchGate\nSee discussions, stats, and author profiles for this publication "
                "at researchgate.net/publication/123\nCitations 10 Reads 300"
            )
            wrapped_path = input_dir / "researchgate.pdf"
            write_pdf(wrapped_path, [cover, *academic_pages()])
            wrapped = self._scan_twice(controller).items[0]
            self.assertIsNotNone(wrapped.duplicate)
            self.assertEqual(wrapped.duplicate.match.kind, DuplicateKind.SAME_WORK)
            operation = controller.trash_confirmed_duplicate(wrapped)
            self.assertFalse(wrapped_path.exists())
            self.assertTrue(operation.trashed_path.exists())
            manifest = json.loads(operation.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], "unorganized_duplicate")
            self.assertIsNone(manifest["restored_at"])
            self.assertEqual(len(controller.analysis_queue()), 1)
            self.assertEqual(
                controller.analysis_queue()[0].title, "Published Paper"
            )
            restored = controller.restore_trash(controller.list_trash()[0])
            self.assertTrue(restored.is_file())
            self.assertEqual(controller.list_trash(), [])
            self.assertEqual(len(controller.analysis_queue()), 2)


if __name__ == "__main__":
    unittest.main()
