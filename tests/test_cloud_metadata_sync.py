import json
import tempfile
import unittest
from pathlib import Path

from paper_organizer.application.cloud_metadata_sync import CloudMetadataSynchronizer


def sidecar_record(title: str = "Original Title") -> dict:
    file_hash = "a" * 64
    return {
        "schema_version": 2,
        "id": f"sha256:{file_hash}",
        "file": {
            "current_name": "paper.pdf",
            "relative_path": "papers/Science/General/paper.pdf",
            "sha256": file_hash,
            "size_bytes": 100,
            "page_count": 5,
        },
        "identity": {
            "file_id": f"sha256:{file_hash}",
            "edition_id": f"sha256:{file_hash}",
            "work_id": "doi:10.1000/cloud-test",
            "source_variant": "publisher",
        },
        "bibliography": {"title": title, "authors": ["A. Author"], "year": 2026},
        "classification": {"category": "Science", "subcategory": "General", "tags": []},
        "description": {"summary_ko": "원본 설명", "keywords": []},
        "experimental_details": {"culture_media": [{"name": "DMEM"}]},
        "workflow": {"status": "organized", "updated_at": "2026-01-01T00:00:00Z"},
        "curation": {"revision": 1, "field_sources": {}, "locked_fields": []},
    }


def write_sidecar(library: Path, record: dict) -> Path:
    path = library / "papers" / "Science" / "General" / "paper.pdf.paper.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def edit_portable(path: Path, title: str) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["papers"][0]["bibliography"]["title"] = title
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class CloudMetadataSyncTests(unittest.TestCase):
    def test_cloud_only_edit_is_imported_with_local_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = root / "library"
            cloud = root / "OneDrive"
            sidecar = write_sidecar(library, sidecar_record())
            sync = CloudMetadataSynchronizer(library, cloud)
            first = sync.synchronize()
            self.assertEqual(first.exported_records, 1)
            self.assertEqual(first.conflicts, ())
            self.assertNotIn(str(root), sync.portable_path.read_text(encoding="utf-8"))
            edit_portable(sync.portable_path, "Cloud Edited Title")
            second = sync.synchronize()
            self.assertEqual(second.imported_records, 1)
            self.assertEqual(second.conflicts, ())
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(saved["bibliography"]["title"], "Cloud Edited Title")
            self.assertEqual(saved["curation"]["last_edited_by"], "cloud_sync")
            self.assertTrue(list((library / "history").rglob("revision-0001.paper.json")))
            index = json.loads((library / "index" / "library.json").read_text(encoding="utf-8"))
            self.assertEqual(index["works"][0]["title"], "Cloud Edited Title")

    def test_two_sided_edit_reports_conflict_and_local_choice_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = root / "library"
            cloud = root / "OneDrive"
            sidecar = write_sidecar(library, sidecar_record())
            sync = CloudMetadataSynchronizer(library, cloud)
            sync.synchronize()
            local = json.loads(sidecar.read_text(encoding="utf-8"))
            local["bibliography"]["title"] = "Local Edit"
            sidecar.write_text(json.dumps(local, ensure_ascii=False), encoding="utf-8")
            edit_portable(sync.portable_path, "Cloud Edit")
            outcome = sync.synchronize()
            self.assertEqual(len(outcome.conflicts), 1)
            self.assertEqual(outcome.conflicts[0].kind, "both_changed")
            self.assertEqual(
                json.loads(sidecar.read_text(encoding="utf-8"))["bibliography"]["title"],
                "Local Edit",
            )
            resolved = sync.resolve(outcome.conflicts[0].record_id, "local")
            self.assertEqual(resolved.conflicts, ())
            portable = json.loads(sync.portable_path.read_text(encoding="utf-8"))
            self.assertEqual(portable["papers"][0]["bibliography"]["title"], "Local Edit")

    def test_two_sided_edit_can_apply_cloud_without_losing_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = root / "library"
            cloud = root / "OneDrive"
            sidecar = write_sidecar(library, sidecar_record())
            sync = CloudMetadataSynchronizer(library, cloud)
            sync.synchronize()
            local = json.loads(sidecar.read_text(encoding="utf-8"))
            local["bibliography"]["title"] = "Local Edit"
            sidecar.write_text(json.dumps(local, ensure_ascii=False), encoding="utf-8")
            edit_portable(sync.portable_path, "Chosen Cloud Edit")
            conflict = sync.synchronize().conflicts[0]
            resolved = sync.resolve(conflict.record_id, "cloud")
            self.assertEqual(resolved.conflicts, ())
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertEqual(saved["bibliography"]["title"], "Chosen Cloud Edit")
            backup = json.loads(
                next((library / "history").rglob("revision-0001.paper.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(backup["bibliography"]["title"], "Local Edit")

    def test_cloud_deletion_never_deletes_local_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = root / "library"
            cloud = root / "OneDrive"
            sidecar = write_sidecar(library, sidecar_record())
            sync = CloudMetadataSynchronizer(library, cloud)
            sync.synchronize()
            portable = json.loads(sync.portable_path.read_text(encoding="utf-8"))
            portable["papers"] = []
            sync.portable_path.write_text(json.dumps(portable), encoding="utf-8")
            outcome = sync.synchronize()
            self.assertEqual(outcome.conflicts[0].kind, "cloud_deleted")
            self.assertTrue(sidecar.is_file())


if __name__ == "__main__":
    unittest.main()
