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


if __name__ == "__main__":
    unittest.main()
