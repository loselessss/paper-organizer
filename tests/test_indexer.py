import json
import tempfile
import unittest
from pathlib import Path

from paper_organizer.core.indexer import rebuild_library_index


def record(file_id: str, relative_path: str, variant: str) -> dict:
    return {
        "schema_version": 2,
        "file": {
            "relative_path": relative_path,
            "size_bytes": 100,
            "page_count": 5,
        },
        "identity": {
            "file_id": file_id,
            "edition_id": file_id,
            "work_id": "doi:10.1000/example",
            "source_variant": variant,
        },
        "bibliography": {
            "title": "Example Paper",
            "authors": ["A. Researcher"],
            "year": 2025,
            "venue": "Nature Methods",
        },
        "classification": {
            "category": "Medicine & Life Science",
            "subcategory": "Cell Biology",
            "tags": ["culture"],
        },
        "description": {
            "summary_ko": "세포 배양 조건을 비교한다.",
            "keywords": ["A549"],
        },
        "experimental_details": {
            "culture_media": [
                {
                    "name": "DMEM",
                    "normalized_name": "Dulbecco's Modified Eagle Medium",
                    "supplements": ["10% FBS"],
                    "used_with": ["A549"],
                }
            ]
        },
    }


class IndexerTests(unittest.TestCase):
    def test_groups_variants_and_prefers_publisher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            papers = root / "papers"
            papers.mkdir()
            (papers / "rg.pdf.paper.json").write_text(
                json.dumps(record("sha256:rg", "papers/rg.pdf", "researchgate")),
                encoding="utf-8",
            )
            (papers / "publisher.pdf.paper.json").write_text(
                json.dumps(record("sha256:pub", "papers/publisher.pdf", "publisher")),
                encoding="utf-8",
            )
            index, problems = rebuild_library_index(root)
            self.assertEqual(problems, [])
            self.assertEqual(index["work_count"], 1)
            self.assertEqual(index["file_count"], 2)
            work = index["works"][0]
            self.assertEqual(work["representative_file_id"], "sha256:pub")
            self.assertEqual(work["venue"], "Nature Methods")
            self.assertIn("nature methods", work["search_text"])
            self.assertIn("dmem", work["search_text"])
            saved = json.loads(
                (root / "index" / "library.json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved["work_count"], 1)

    def test_bad_sidecar_is_reported_without_blocking_rebuild(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            papers = root / "papers"
            papers.mkdir()
            (papers / "bad.pdf.paper.json").write_text("not json", encoding="utf-8")
            index, problems = rebuild_library_index(root)
            self.assertEqual(index["work_count"], 0)
            self.assertEqual(len(problems), 1)
            error_data = json.loads(
                (root / "index" / "errors.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(error_data["problems"]), 1)


if __name__ == "__main__":
    unittest.main()
