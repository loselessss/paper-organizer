import unittest
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
import zipfile

from scripts.create_release_source import _safe_archive_name, build_information, create_release_source


class ReleaseSourceTests(unittest.TestCase):
    def test_archive_paths_reject_escape_and_windows_separators(self):
        self.assertTrue(_safe_archive_name("paper_organizer/gui.py"))
        self.assertFalse(_safe_archive_name("../secret.txt"))
        self.assertFalse(_safe_archive_name("folder\\secret.txt"))

    def test_build_document_records_both_source_revisions(self):
        with patch("scripts.create_release_source._package_inventory", return_value=[("PyQt5", "5.15.11")]):
            document = build_information("2.5.0", "a" * 40, "b" * 40)
        self.assertIn("Paper Organizer: [" + "a" * 40, document)
        self.assertIn("Bundled sPDF: [" + "b" * 40, document)
        self.assertIn("PyQt5/5.15.11/#files", document)
        self.assertIn("build_installer.bat", document)

    def test_source_archive_contains_main_and_pinned_spdf_sources(self):
        def git(root, *args):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spdf = root / "vendor" / "spdf"
            spdf.mkdir(parents=True)
            git(spdf, "init")
            git(spdf, "config", "user.name", "Test")
            git(spdf, "config", "user.email", "test@example.invalid")
            (spdf / "reader.py").write_text("reader = True\n", encoding="utf-8")
            git(spdf, "add", "reader.py")
            git(spdf, "commit", "-m", "reader")
            git(root, "init")
            git(root, "config", "user.name", "Test")
            git(root, "config", "user.email", "test@example.invalid")
            (root / "app.py").write_text("app = True\n", encoding="utf-8")
            git(root, "add", "app.py", "vendor/spdf")
            git(root, "commit", "-m", "release")
            git(root, "tag", "v2.5.0")
            with patch("scripts.create_release_source._package_inventory", return_value=[]):
                archive = create_release_source(root, "2.5.0")
            with zipfile.ZipFile(archive) as source:
                names = set(source.namelist())
            self.assertIn("PaperOrganizer-2.5.0/app.py", names)
            self.assertIn("PaperOrganizer-2.5.0/vendor/spdf/reader.py", names)
            self.assertIn("PaperOrganizer-2.5.0/DEPENDENCY_SOURCES_AND_BUILD.md", names)
            self.assertTrue(Path(str(archive) + ".sha256").is_file())
