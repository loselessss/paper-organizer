import hashlib
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from paper_organizer.core.paperpack import (
    CONTENT_ENTRY,
    MANIFEST_ENTRY,
    METADATA_ENTRY,
    MIMETYPE_ENTRY,
    PDF_ENTRY,
    PaperPackError,
    create_paperpack,
    extract_paperpack_pdf,
    extract_paperpack_pdfs,
    inspect_paperpack,
    import_pdf_to_paperpack,
    load_paperpack_content,
    load_paperpack_metadata,
    replace_paperpack_pdf,
    update_paperpack,
    verify_paperpack,
)


PDF_BYTES = b"%PDF-1.7\nminimal test document\n%%EOF\n"


class PaperPackTests(unittest.TestCase):
    def _create(self, root: Path) -> tuple[Path, Path]:
        pdf = root / "paper.pdf"
        pdf.write_bytes(PDF_BYTES)
        pack = root / "paper.paperpack"
        create_paperpack(
            pack,
            pdf,
            {"identity": {"work_id": "doi:10.1000/test"}, "title": "Before"},
            content={"chunks": [{"text": "DMEM", "page": 3}]},
        )
        return pdf, pack

    def test_create_stores_portable_pdf_and_json_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            pdf, pack = self._create(Path(temp))
            self.assertTrue(zipfile.is_zipfile(pack))
            self.assertTrue(pdf.is_file())
            with zipfile.ZipFile(pack) as archive:
                names = set(archive.namelist())
                self.assertTrue(
                    {MIMETYPE_ENTRY, MANIFEST_ENTRY, PDF_ENTRY, METADATA_ENTRY, CONTENT_ENTRY}
                    .issubset(names)
                )
                self.assertEqual(archive.read(PDF_ENTRY), PDF_BYTES)
            info = inspect_paperpack(pack)
            self.assertEqual(info.original_name, "paper.pdf")
            self.assertEqual(info.pdf_sha256, hashlib.sha256(PDF_BYTES).hexdigest())
            self.assertEqual(info.revision, 1)
            self.assertEqual(load_paperpack_metadata(pack)["title"], "Before")
            self.assertEqual(load_paperpack_content(pack)["chunks"][0]["text"], "DMEM")

    def test_create_migrates_legacy_summary_field_without_persisting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "paper.pdf"
            pdf.write_bytes(PDF_BYTES)
            pack = root / "paper.paperpack"
            create_paperpack(
                pack,
                pdf,
                {
                    "description": {"summary_ko": "기존 요약"},
                    "analysis": {"summary_ko": "기존 AI 요약"},
                    "curation": {
                        "field_sources": {
                            "description.summary_ko": "user",
                        },
                        "locked_fields": ["description.summary_ko"],
                    },
                },
            )

            metadata = load_paperpack_metadata(pack)
            self.assertEqual(metadata["description"]["summary"], "기존 요약")
            self.assertEqual(metadata["analysis"]["summary"], "기존 AI 요약")
            self.assertNotIn("summary_ko", metadata["description"])
            self.assertEqual(
                metadata["curation"]["field_sources"],
                {"description.summary": "user"},
            )
            self.assertEqual(
                metadata["curation"]["locked_fields"],
                ["description.summary"],
            )
            with zipfile.ZipFile(pack) as archive:
                raw = json.loads(archive.read(METADATA_ENTRY))
            self.assertNotIn("summary_ko", json.dumps(raw, ensure_ascii=False))

    def test_import_keeps_source_pdf_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "source.pdf"
            pdf.write_bytes(PDF_BYTES)
            result = import_pdf_to_paperpack(
                root / "paper.paperpack", pdf, {"title": "Keep"}
            )
            self.assertFalse(result.source_removed)
            self.assertTrue(pdf.is_file())
            self.assertTrue(result.paperpack.path.is_file())

    def test_import_can_remove_source_only_after_verified_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "source.pdf"
            pdf.write_bytes(PDF_BYTES)
            result = import_pdf_to_paperpack(
                root / "paper.paperpack",
                pdf,
                {"title": "Move"},
                remove_source=True,
            )
            self.assertTrue(result.source_removed)
            self.assertFalse(pdf.exists())
            self.assertEqual(
                extract_paperpack_pdf(
                    result.paperpack.path, root / "restored.pdf"
                ).read_bytes(),
                PDF_BYTES,
            )

    def test_failed_import_never_removes_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "source.pdf"
            pdf.write_text("not a PDF", encoding="utf-8")
            pack = root / "paper.paperpack"
            with self.assertRaises(PaperPackError):
                import_pdf_to_paperpack(pack, pdf, {}, remove_source=True)
            self.assertTrue(pdf.is_file())
            self.assertFalse(pack.exists())

    def test_metadata_update_is_atomic_and_keeps_revision_history(self):
        with tempfile.TemporaryDirectory() as temp:
            _pdf, pack = self._create(Path(temp))
            info = update_paperpack(pack, {"title": "After"}, changed_by="curator")
            self.assertEqual(info.revision, 2)
            self.assertEqual(load_paperpack_metadata(pack)["title"], "After")
            self.assertEqual(load_paperpack_content(pack)["chunks"][0]["text"], "DMEM")
            with zipfile.ZipFile(pack) as archive:
                self.assertIn("history/revision-0001.json", archive.namelist())
                self.assertIn("history/revision-0002.json", archive.namelist())
                self.assertEqual(archive.read(PDF_ENTRY), PDF_BYTES)

    def test_extract_restores_the_exact_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            _pdf, pack = self._create(Path(temp))
            extracted = extract_paperpack_pdf(pack, Path(temp) / "cache" / "open.pdf")
            self.assertEqual(extracted.read_bytes(), PDF_BYTES)

    def test_verify_detects_duplicate_tampered_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            _pdf, pack = self._create(Path(temp))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(pack, "a") as archive:
                    archive.writestr(PDF_ENTRY, b"damaged")
            with self.assertRaisesRegex(PaperPackError, "duplicate entries"):
                verify_paperpack(pack)

    def test_update_preserves_unknown_safe_extension_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            _pdf, pack = self._create(Path(temp))
            with zipfile.ZipFile(pack, "a") as archive:
                archive.writestr("extensions/example/data.txt", b"future data")
            update_paperpack(pack, {"title": "Updated"})
            with zipfile.ZipFile(pack) as archive:
                self.assertEqual(
                    archive.read("extensions/example/data.txt"), b"future data"
                )

    def test_replace_pdf_updates_manifest_metadata_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _pdf, pack = self._create(root)
            before = inspect_paperpack(pack)
            edited = root / "edited.pdf"
            edited_bytes = PDF_BYTES + b"\n% edited annotation\n"
            edited.write_bytes(edited_bytes)
            metadata = {
                "identity": {"work_id": "doi:10.1000/test"},
                "title": "Edited PDF",
            }

            after = replace_paperpack_pdf(
                pack,
                edited,
                metadata,
                expected_pdf_sha256=before.pdf_sha256,
                expected_revision=before.revision,
            )

            self.assertEqual(after.revision, 2)
            self.assertEqual(after.pdf_sha256, hashlib.sha256(edited_bytes).hexdigest())
            self.assertEqual(load_paperpack_metadata(pack)["title"], "Edited PDF")
            with zipfile.ZipFile(pack) as archive:
                self.assertEqual(archive.read(PDF_ENTRY), edited_bytes)
                history = json.loads(archive.read("history/revision-0002.json"))
                self.assertEqual(history["change"]["kind"], "pdf_replaced")
                self.assertEqual(
                    history["change"]["previous_pdf_sha256"], before.pdf_sha256
                )

    def test_replace_pdf_conflict_and_invalid_input_leave_pack_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _pdf, pack = self._create(root)
            original = pack.read_bytes()
            edited = root / "edited.pdf"
            edited.write_bytes(PDF_BYTES + b"changed")
            metadata = load_paperpack_metadata(pack)

            with self.assertRaisesRegex(PaperPackError, "changed after"):
                replace_paperpack_pdf(
                    pack,
                    edited,
                    metadata,
                    expected_pdf_sha256="0" * 64,
                )
            self.assertEqual(pack.read_bytes(), original)

            edited.write_text("not a PDF", encoding="utf-8")
            with self.assertRaisesRegex(PaperPackError, "not a PDF"):
                replace_paperpack_pdf(pack, edited, metadata)
            self.assertEqual(pack.read_bytes(), original)

    def test_replace_pdf_install_failure_leaves_original_pack(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _pdf, pack = self._create(root)
            original = pack.read_bytes()
            edited = root / "edited.pdf"
            edited.write_bytes(PDF_BYTES + b"changed")
            with mock.patch(
                "paper_organizer.core.paperpack.os.replace",
                side_effect=OSError("simulated install failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated"):
                    replace_paperpack_pdf(
                        pack,
                        edited,
                        load_paperpack_metadata(pack),
                    )
            self.assertEqual(pack.read_bytes(), original)

    def test_rejects_wrong_extension_and_non_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text = root / "not-paper.pdf"
            text.write_text("hello", encoding="utf-8")
            with self.assertRaises(PaperPackError):
                create_paperpack(root / "bad.paperpack", text, {})
            text.write_bytes(PDF_BYTES)
            with self.assertRaises(PaperPackError):
                create_paperpack(root / "bad.zip", text, {})

    def test_batch_extracts_multiple_pdfs_and_keeps_sources_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_pdf = root / "first" / "paper.pdf"
            second_pdf = root / "second" / "paper.pdf"
            first_pdf.parent.mkdir()
            second_pdf.parent.mkdir()
            first_pdf.write_bytes(PDF_BYTES + b"first")
            second_pdf.write_bytes(PDF_BYTES + b"second")
            first_pack = root / "first.paperpack"
            second_pack = root / "second.paperpack"
            create_paperpack(first_pack, first_pdf, {"title": "First"})
            create_paperpack(second_pack, second_pdf, {"title": "Second"})
            result = extract_paperpack_pdfs(
                [first_pack, second_pack], root / "handoff"
            )
            self.assertFalse(result.sources_removed)
            self.assertTrue(first_pack.is_file())
            self.assertTrue(second_pack.is_file())
            self.assertEqual(
                [item.pdf_path.name for item in result.items],
                ["paper.pdf", "paper (2).pdf"],
            )
            self.assertEqual(result.items[0].pdf_path.read_bytes(), first_pdf.read_bytes())
            self.assertEqual(result.items[1].pdf_path.read_bytes(), second_pdf.read_bytes())

    def test_batch_can_remove_sources_only_after_all_outputs_verify(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_pdf = root / "first.pdf"
            second_pdf = root / "second.pdf"
            first_pdf.write_bytes(PDF_BYTES + b"first")
            second_pdf.write_bytes(PDF_BYTES + b"second")
            first_pack = root / "first.paperpack"
            second_pack = root / "second.paperpack"
            create_paperpack(first_pack, first_pdf, {})
            create_paperpack(second_pack, second_pdf, {})
            result = extract_paperpack_pdfs(
                [first_pack, second_pack],
                root / "handoff",
                remove_sources=True,
            )
            self.assertTrue(result.sources_removed)
            self.assertFalse(first_pack.exists())
            self.assertFalse(second_pack.exists())
            self.assertTrue(all(item.pdf_path.is_file() for item in result.items))

    def test_bad_pack_prevents_all_source_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_pdf = root / "first.pdf"
            second_pdf = root / "second.pdf"
            first_pdf.write_bytes(PDF_BYTES + b"first")
            second_pdf.write_bytes(PDF_BYTES + b"second")
            first_pack = root / "first.paperpack"
            second_pack = root / "second.paperpack"
            create_paperpack(first_pack, first_pdf, {})
            create_paperpack(second_pack, second_pdf, {})
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(second_pack, "a") as archive:
                    archive.writestr(PDF_ENTRY, b"damaged")
            with self.assertRaises(PaperPackError):
                extract_paperpack_pdfs(
                    [first_pack, second_pack],
                    root / "handoff",
                    remove_sources=True,
                )
            self.assertTrue(first_pack.is_file())
            self.assertTrue(second_pack.is_file())
            self.assertFalse((root / "handoff" / "first.pdf").exists())


if __name__ == "__main__":
    unittest.main()
