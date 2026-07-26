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
    extract_paperpack_pdf,
    inspect_paperpack,
    load_paperpack_metadata,
    update_paperpack,
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
    def _controller(self, root: Path, *, remove_source: bool = False):
        input_dir = root / "downloads"
        library = root / "library"
        input_dir.mkdir()
        settings_path = root / "settings.json"
        controller = LibraryWorkflowController(settings_path)
        controller.save_paths(
            input_dir,
            library,
            auto_enabled=False,
            remove_source_after_import=remove_source,
            auto_organize_academic=False,
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

    def test_organize_writes_paperpack_and_rebuilds_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
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

    def test_spdf_working_copy_applies_as_new_paperpack_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root)
            source = input_dir / "paper.pdf"
            write_pdf(source, academic_pages())
            item = self._scan_twice(controller).items[0]
            organized = controller.organize(
                item, EditablePaperMetadata(title="Editable Paper")
            )
            before = inspect_paperpack(organized.pdf_path)

            working = controller.materialize_editable_pdf(organized.pdf_path)
            self.assertNotEqual(working, controller.materialize_pdf(organized.pdf_path))
            with working.open("ab") as stream:
                stream.write(b"\n% sPDF saved annotation\n")
            pending = controller.paperpack_working_copy(organized.pdf_path)
            self.assertIsNotNone(pending)
            self.assertTrue(pending.changed)
            self.assertFalse(pending.conflicted)

            result = controller.apply_paperpack_working_copy(organized.pdf_path)

            self.assertEqual(result.revision, before.revision + 1)
            self.assertNotEqual(result.pdf_sha256, before.pdf_sha256)
            saved = load_paperpack_metadata(organized.pdf_path)
            self.assertEqual(saved["file"]["sha256"], result.pdf_sha256)
            self.assertTrue(saved["workflow"]["needs_reanalysis"])
            self.assertFalse(saved["workflow"]["content_stale"])
            from paper_organizer.core.paperpack import (
                content_pages,
                load_paperpack_content,
            )

            self.assertTrue(
                content_pages(load_paperpack_content(organized.pdf_path))
            )
            extracted = extract_paperpack_pdf(
                organized.pdf_path, root / "after-edit.pdf"
            )
            self.assertEqual(extracted.read_bytes(), working.read_bytes())
            queue = controller.analysis_queue()
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0].file_sha256, result.pdf_sha256)
            self.assertEqual(queue[0].status, "organized_pending_analysis")
            clean = controller.paperpack_working_copy(organized.pdf_path)
            self.assertFalse(clean.changed)
            self.assertFalse(clean.conflicted)

    def test_spdf_working_copy_detects_conflict_and_can_be_discarded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root)
            write_pdf(input_dir / "paper.pdf", academic_pages())
            item = self._scan_twice(controller).items[0]
            organized = controller.organize(
                item, EditablePaperMetadata(title="Conflict Paper")
            )
            working = controller.materialize_editable_pdf(organized.pdf_path)
            with working.open("ab") as stream:
                stream.write(b"\n% local edit\n")
            update_paperpack(
                organized.pdf_path,
                load_paperpack_metadata(organized.pdf_path),
                changed_by="concurrent-test",
            )

            status = controller.paperpack_working_copy(organized.pdf_path)
            self.assertTrue(status.changed)
            self.assertTrue(status.conflicted)
            with self.assertRaisesRegex(Exception, "변경되었습니다"):
                controller.apply_paperpack_working_copy(organized.pdf_path)
            self.assertTrue(controller.discard_paperpack_working_copy(organized.pdf_path))
            self.assertFalse(working.exists())
            self.assertIsNone(controller.paperpack_working_copy(organized.pdf_path))

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

    def test_new_pdf_can_be_discarded_and_ignored_until_restored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root)
            source = input_dir / "discard.pdf"
            write_pdf(source, academic_pages())
            item = self._scan_twice(controller).items[0]

            operation = controller.trash_confirmed_duplicate(item)
            manifest = json.loads(operation.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["kind"], "discarded_new_pdf")
            self.assertFalse(source.exists())

            restored = controller.restore_trash(controller.list_trash()[0])
            self.assertTrue(restored.exists())
            self.assertEqual(len(self._scan_twice(controller).items), 1)

    def test_changing_library_root_moves_database_and_state_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            marker = library / "index" / "search.sqlite"
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b"database")
            state = library / "state" / "analysis-queue.json"
            state.parent.mkdir(parents=True)
            state.write_text('{"schema_version":1,"items":[]}', encoding="utf-8")
            destination = root / "moved-library"

            controller.save_paths(
                input_dir,
                destination,
                auto_enabled=False,
            )

            self.assertFalse(marker.exists())
            self.assertEqual(
                (destination / "index" / "search.sqlite").read_bytes(), b"database"
            )
            self.assertTrue((destination / "state" / "analysis-queue.json").is_file())


if __name__ == "__main__":
    unittest.main()
