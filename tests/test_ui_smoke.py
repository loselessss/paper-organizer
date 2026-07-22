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
        from PyQt5.QtWidgets import QLabel, QLineEdit

        from paper_organizer.application.ai_settings import AiSettingsController
        from paper_organizer.application.summary_service import (
            ImmediateSummaryController,
        )
        from paper_organizer.application.library_workflow import LibraryWorkflowController
        from paper_organizer.ui.ai_settings_dialog import AiSettingsDialog
        from paper_organizer.ui.main_window import PaperOrganizerWindow
        from paper_organizer.ui.startup_splash import CREATOR, create_splash

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = MemorySecretStore()
            ai_controller = AiSettingsController(store, path)
            summary_controller = ImmediateSummaryController(store, path)
            workflow_controller = LibraryWorkflowController(path)
            dialog = AiSettingsDialog(ai_controller)
            window = PaperOrganizerWindow(
                ai_controller, summary_controller, workflow_controller
            )
            splash = create_splash()

            self.assertEqual(dialog.key_edit.echoMode(), QLineEdit.Password)
            self.assertFalse(window.summary_widget.run_button.isEnabled())
            self.assertEqual(window.tabs.tabText(0), "수집 및 검토")
            self.assertEqual(window.tabs.tabText(1), "분석 큐")
            self.assertEqual(window.tabs.tabText(3), "클라우드 동기화")
            self.assertEqual(window.tabs.tabText(4), "즉시 요약")
            self.assertTrue(window.collection_widget.input_edit.text().endswith("Downloads"))
            self.assertEqual(window.collection_widget.interval_spin.value(), 300)
            self.assertEqual(CREATOR, "SANGKYU SHIN, Ph.D.")
            splash_labels = {label.text() for label in splash.findChildren(QLabel)}
            self.assertIn("Paper Organizer", splash_labels)
            self.assertIn("Version 0.3.0", splash_labels)
            self.assertIn("Created by SANGKYU SHIN, Ph.D.", splash_labels)
            splash.close()
            dialog.close()
            window.close()


if __name__ == "__main__":
    unittest.main()
