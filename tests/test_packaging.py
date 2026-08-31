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
        self.assertIn("paper_organizer/ocr_worker_main.py", spec)
        self.assertIn("korean_PP-OCRv5_rec_mobile.onnx", spec)
        self.assertIn("llama-server.exe", spec)
        self.assertIn('"llm/cpu"', spec)
        self.assertIn('"llm/vulkan"', spec)
        self.assertNotIn('"llm/cuda"', spec)
        self.assertIn("PaperOrganizer-ocr", build_script)
        self.assertIn("PaperOrganizer\\ocr", build_script)

    def test_version_tags_build_and_publish_a_windows_installer(self):
        workflow = (
            ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('      - "v*.*.*"', workflow)
        self.assertIn("choco install innosetup", workflow)
        self.assertIn("cmd /c build_installer.bat", workflow)
        self.assertIn("Get-FileHash -Algorithm SHA256", workflow)
        self.assertIn("PaperOrganizer_Setup_latest.exe", workflow)
        self.assertIn("$latestInstaller $latestChecksum", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("contents: write", workflow)


if __name__ == "__main__":
    unittest.main()
