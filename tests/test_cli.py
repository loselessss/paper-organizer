import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import fitz

from paper_organizer.cli import main


class CliTests(unittest.TestCase):
    def test_identity_command_reads_real_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text(
                (50, 70),
                "Example Paper\nAbstract\nThis paper presents a tested method.\n"
                "Introduction\nThe method is evaluated on several datasets.",
            )
            document.save(path)
            document.close()

            output = io.StringIO()
            with redirect_stdout(output):
                result = main(["identity", str(path)])
            identity = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertTrue(identity["file_id"].startswith("sha256:"))
            self.assertEqual(identity["page_count"], 1)

    def test_paperpack_create_inspect_and_extract_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "paper.pdf"
            document = fitz.open()
            document.new_page()
            document.save(pdf)
            document.close()
            metadata = root / "metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "file": {"relative_path": "papers/paper.paperpack"},
                        "identity": {"work_id": "doi:10.1000/cli"},
                        "bibliography": {"title": "CLI Paper"},
                    }
                ),
                encoding="utf-8",
            )
            pack = root / "paper.paperpack"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["paperpack", "create", str(pdf), str(metadata), str(pack)]),
                    0,
                )
            inspected = io.StringIO()
            with redirect_stdout(inspected):
                self.assertEqual(main(["paperpack", "inspect", str(pack)]), 0)
            self.assertEqual(
                json.loads(inspected.getvalue())["metadata"]["bibliography"]["title"],
                "CLI Paper",
            )
            extracted = root / "extracted.pdf"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["paperpack", "extract", str(pack), str(extracted)]), 0
                )
            self.assertEqual(extracted.read_bytes(), pdf.read_bytes())

    def test_paperpack_extract_many_requires_explicit_delete_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "confirm-remove-source"):
                main(
                    [
                        "paperpack",
                        "extract-many",
                        str(root),
                        "--output-dir",
                        str(root / "pdfs"),
                        "--remove-source",
                    ]
                )

    def test_paperpack_create_requires_explicit_source_delete_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.7\nfixture\n%%EOF\n")
            metadata = root / "metadata.json"
            metadata.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "confirm-remove-source"):
                main(
                    [
                        "paperpack",
                        "create",
                        str(pdf),
                        str(metadata),
                        str(root / "paper.paperpack"),
                        "--remove-source",
                    ]
                )
            self.assertTrue(pdf.is_file())

    def test_legacy_migration_cleanup_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "confirm-move-legacy"):
                main(
                    [
                        "paperpack",
                        "migrate-legacy",
                        str(root),
                        "--move-legacy-to-trash",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
