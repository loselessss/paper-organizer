import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


HAS_PYQT5 = importlib.util.find_spec("PyQt5") is not None


class MemorySecretStore:
    def __init__(self):
        self.values = {}

    def get(self, provider):
        return self.values.get(provider)

    def set(self, provider, secret):
        self.values[provider] = secret

    def delete(self, provider):
        self.values.pop(provider, None)


@unittest.skipUnless(HAS_PYQT5, "PyQt5 optional dependency is not installed")
class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_ai_settings_and_summary_shell_construct(self):
        from PyQt5.QtWidgets import QLineEdit

        from paper_organizer.application.ai_settings import AiSettingsController
        from paper_organizer.application.summary_service import (
            ImmediateSummaryController,
        )
        from paper_organizer.ui.ai_settings_dialog import AiSettingsDialog
        from paper_organizer.ui.main_window import PaperOrganizerWindow

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = MemorySecretStore()
            ai_controller = AiSettingsController(store, path)
            summary_controller = ImmediateSummaryController(store, path)
            dialog = AiSettingsDialog(ai_controller)
            window = PaperOrganizerWindow(ai_controller, summary_controller)

            self.assertEqual(dialog.key_edit.echoMode(), QLineEdit.Password)
            self.assertFalse(window.summary_widget.run_button.isEnabled())
            self.assertEqual(window.tabs.tabText(0), "즉시 요약")
            dialog.close()
            window.close()


if __name__ == "__main__":
    unittest.main()
