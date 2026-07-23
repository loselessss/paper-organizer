import re
import tomllib
import unittest
from pathlib import Path

from paper_organizer import __version__


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_release_versions_match(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        installer = (ROOT / "installer.iss").read_text(encoding="utf-8")
        match = re.search(r'^#define MyAppVersion "([^"]+)"$', installer, re.MULTILINE)

        self.assertIsNotNone(match)
        self.assertEqual(project["project"]["version"], __version__)
        self.assertEqual(match.group(1), __version__)

    def test_installer_contains_app_ocr_and_optional_startup(self):
        installer = (ROOT / "installer.iss").read_text(encoding="utf-8")

        self.assertIn('Source: "dist\\PaperOrganizer\\*"', installer)
        self.assertIn("recursesubdirs", installer)
        self.assertIn('Name: "startup"', installer)
        self.assertIn("Flags: unchecked", installer)
        self.assertIn("uninsdeletevalue", installer)

    def test_build_scripts_use_isolated_ocr_worker(self):
        spec = (ROOT / "paper-organizer.spec").read_text(encoding="utf-8")
        build_script = (ROOT / "build_exe.bat").read_text(encoding="utf-8")

        self.assertIn('name="spdf-ocr"', spec)
        self.assertIn("korean_PP-OCRv5_rec_mobile.onnx", spec)
        self.assertIn("PaperOrganizer-ocr", build_script)
        self.assertIn("PaperOrganizer\\ocr", build_script)


if __name__ == "__main__":
    unittest.main()
