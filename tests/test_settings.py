import json
import tempfile
import unittest
from pathlib import Path

from paper_organizer.infra.settings import AppSettings, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            expected = AppSettings(
                input_dir=str(Path(temp) / "input"),
                library_root=str(Path(temp) / "library"),
                auto_enabled=True,
                selected_model="qwen3:4b",
            )
            save_settings(expected, path)
            self.assertEqual(load_settings(path), expected)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["resource_profile"], "eco")

    def test_same_input_and_library_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = AppSettings(input_dir=temp, library_root=temp)
            with self.assertRaises(ValueError):
                settings.validate()

    def test_invalid_json_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_settings(path), AppSettings())


if __name__ == "__main__":
    unittest.main()
