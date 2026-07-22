import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_organizer.application.legacy_migration import (
    LegacyMigrationError,
    LegacyMigrationService,
)
from paper_organizer.core.paperpack import (
    create_paperpack as real_create_paperpack,
    load_paperpack_content,
    load_paperpack_metadata,
)


def legacy_record(name: str, file_id: str) -> dict:
    return {
        "schema_version": 2,
        "id": file_id,
        "file": {
            "current_name": f"{name}.pdf",
            "relative_path": f"papers/Science/{name}.pdf",
            "sha256": file_id.removeprefix("sha256:"),
            "size_bytes": 30,
            "page_count": 1,
        },
        "identity": {
            "file_id": file_id,
            "edition_id": file_id,
            "work_id": f"work:{name}",
            "source_variant": "publisher",
        },
        "bibliography": {"title": f"Legacy {name}", "authors": [], "year": 2020},
        "classification": {"category": "Science", "subcategory": "General", "tags": []},
        "description": {"summary_ko": "", "keywords": []},
        "curation": {"revision": 1},
        "workflow": {"status": "organized"},
    }


def write_legacy(root: Path, name: str, file_id: str, *, content: bool = True):
    papers = root / "papers" / "Science"
    papers.mkdir(parents=True, exist_ok=True)
    pdf = papers / f"{name}.pdf"
    pdf.write_bytes(b"%PDF-1.7\nlegacy fixture\n%%EOF\n")
    sidecar = Path(f"{pdf}.paper.json")
    sidecar.write_text(json.dumps(legacy_record(name, file_id)), encoding="utf-8")
    content_path = Path(f"{pdf}.content.json")
    if content:
        content_path.write_text(
            json.dumps({"chunks": [{"page": 1, "text": name}]}),
            encoding="utf-8",
        )
    return pdf, sidecar, content_path


class LegacyMigrationTests(unittest.TestCase):
    def test_preview_and_migrate_keep_legacy_files_by_default(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "library"
            pdf, sidecar, content = write_legacy(root, "paper", "sha256:paper")
            service = LegacyMigrationService(root)
            preview = service.preview()
            self.assertEqual(len(preview.candidates), 1)
            result = service.migrate([sidecar])
            self.assertFalse(result.legacy_moved_to_trash)
            self.assertTrue(pdf.is_file())
            self.assertTrue(sidecar.is_file())
            self.assertTrue(content.is_file())
            pack = result.items[0].paperpack_path
            self.assertTrue(pack.is_file())
            metadata = load_paperpack_metadata(pack)
            self.assertEqual(metadata["file"]["relative_path"], "papers/Science/paper.paperpack")
            self.assertEqual(metadata["file"]["storage_format"], "paperpack-zip-v1")
            self.assertEqual(load_paperpack_content(pack)["chunks"][0]["text"], "paper")
            index = json.loads((root / "index" / "library.json").read_text(encoding="utf-8"))
            self.assertEqual(index["file_count"], 1)
            self.assertEqual(service.preview().already_migrated, 1)

    def test_migrate_can_move_legacy_files_to_recoverable_trash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "library"
            pdf, sidecar, content = write_legacy(root, "paper", "sha256:paper")
            service = LegacyMigrationService(root)
            result = service.migrate(
                [sidecar], move_legacy_to_trash=True
            )
            self.assertTrue(result.legacy_moved_to_trash)
            self.assertIsNotNone(result.trash_operation_id)
            self.assertFalse(pdf.exists())
            self.assertFalse(sidecar.exists())
            self.assertFalse(content.exists())
            self.assertTrue(result.items[0].paperpack_path.is_file())
            manifest = root / "trash" / str(result.trash_operation_id) / "manifest.json"
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(saved["kind"], "legacy-paperpack-migration")
            self.assertEqual(len(saved["files"]), 3)
            self.assertEqual(len(service.list_trash()), 1)
            restored = service.restore_trash(str(result.trash_operation_id))
            self.assertEqual(len(restored), 3)
            self.assertTrue(pdf.is_file())
            self.assertTrue(sidecar.is_file())
            self.assertTrue(content.is_file())
            self.assertTrue(result.items[0].paperpack_path.is_file())
            self.assertEqual(service.list_trash(), ())

    def test_invalid_content_is_reported_and_not_migrated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "library"
            pdf, sidecar, content = write_legacy(root, "paper", "sha256:paper")
            content.write_text("not json", encoding="utf-8")
            preview = LegacyMigrationService(root).preview()
            self.assertEqual(preview.candidates, ())
            self.assertEqual(len(preview.problems), 1)
            self.assertTrue(pdf.is_file())
            self.assertTrue(sidecar.is_file())

    def test_batch_failure_rolls_back_created_paperpacks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "library"
            _pdf1, sidecar1, _content1 = write_legacy(
                root, "first", "sha256:first"
            )
            _pdf2, sidecar2, _content2 = write_legacy(
                root, "second", "sha256:second"
            )
            calls = 0

            def fail_second(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated write failure")
                return real_create_paperpack(*args, **kwargs)

            service = LegacyMigrationService(root)
            with patch(
                "paper_organizer.application.legacy_migration.create_paperpack",
                side_effect=fail_second,
            ):
                with self.assertRaises(LegacyMigrationError):
                    service.migrate([sidecar1, sidecar2])
            self.assertFalse((root / "papers" / "Science" / "first.paperpack").exists())
            self.assertFalse((root / "papers" / "Science" / "second.paperpack").exists())
            self.assertTrue(sidecar1.is_file())
            self.assertTrue(sidecar2.is_file())


if __name__ == "__main__":
    unittest.main()
