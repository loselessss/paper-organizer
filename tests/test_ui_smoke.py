import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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


class MemoryLoginStartup:
    def __init__(self):
        self.enabled = False

    def set_enabled(self, enabled):
        self.enabled = enabled


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
        from paper_organizer.ui.ollama_model_dialog import OllamaModelDialog
        from paper_organizer.ui.startup_splash import CREATOR, create_splash

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = MemorySecretStore()
            ai_controller = AiSettingsController(store, path)
            summary_controller = ImmediateSummaryController(store, path)
            workflow_controller = LibraryWorkflowController(path)
            dialog = AiSettingsDialog(ai_controller)
            model_dialog = OllamaModelDialog(ai_controller)
            window = PaperOrganizerWindow(
                ai_controller, summary_controller, workflow_controller
            )
            splash = create_splash()

            self.assertEqual(dialog.key_edit.echoMode(), QLineEdit.Password)
            self.assertEqual(dialog.model_profile_combo.currentData(), "auto")
            self.assertEqual(
                dialog.use_recommendation_button.text(),
                "추천 모델 선택 (다운로드 안 함)",
            )
            self.assertFalse(dialog.use_recommendation_button.isEnabled())
            self.assertEqual(dialog.manage_models_button.text(), "Ollama 모델 관리…")
            self.assertEqual(model_dialog.install_button.text(), "다운로드 후 선택")
            self.assertFalse(model_dialog.install_button.isEnabled())
            self.assertFalse(model_dialog.delete_button.isEnabled())
            self.assertFalse(window.summary_widget.run_button.isEnabled())
            self.assertEqual(window.tabs.tabText(0), "수집 및 검토")
            self.assertEqual(window.tabs.tabText(1), "분석 큐")
            self.assertEqual(window.tabs.tabText(2), "라이브러리")
            self.assertEqual(window.tabs.tabText(3), "레거시 변환")
            self.assertEqual(window.tabs.tabText(4), "즉시 요약")
            self.assertTrue(window.collection_widget.input_edit.text().endswith("Downloads"))
            self.assertEqual(window.collection_widget.interval_spin.value(), 300)
            self.assertFalse(window.collection_widget.remove_source_check.isChecked())
            self.assertEqual(
                window.library_widget.apply_pdf_button.text(),
                "편집본을 PaperPack에 적용",
            )
            self.assertFalse(window.library_widget.apply_pdf_button.isEnabled())
            self.assertFalse(window.library_widget.discard_pdf_button.isEnabled())
            self.assertEqual(
                window.collection_widget.form.venue_edit.placeholderText(),
                "저널명 또는 학회명",
            )
            self.assertEqual(CREATOR, "SANGKYU SHIN, Ph.D.")
            splash_labels = {label.text() for label in splash.findChildren(QLabel)}
            self.assertIn("Paper Organizer", splash_labels)
            self.assertIn("Version 0.9.0", splash_labels)
            self.assertEqual(
                window.queue_widget.background_button.text(),
                "백그라운드 분석 시작",
            )
            self.assertEqual(
                window.queue_widget.run_now_button.text(),
                "선택 항목 지금 분석",
            )
            self.assertIn("Created by SANGKYU SHIN, Ph.D.", splash_labels)
            splash.close()
            model_dialog.close()
            dialog.close()
            window.close()

    def test_first_run_requires_an_explicit_close_choice(self):
        from PyQt5.QtWidgets import QDialog

        from paper_organizer.application.lifecycle import LifecycleSettingsController
        from paper_organizer.ui.lifecycle_dialog import LifecyclePreferencesDialog

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            startup = MemoryLoginStartup()
            controller = LifecycleSettingsController(path, startup)
            dialog = LifecyclePreferencesDialog(
                controller,
                first_run=True,
            )

            self.assertFalse(dialog.start_with_windows_check.isChecked())
            self.assertFalse(dialog.background_radio.isChecked())
            self.assertFalse(dialog.quit_radio.isChecked())
            self.assertFalse(dialog.save_button.isEnabled())
            dialog.background_radio.setChecked(True)
            self.assertTrue(dialog.save_button.isEnabled())
            dialog.start_with_windows_check.setChecked(True)
            dialog._save()

            self.assertEqual(dialog.result(), QDialog.Accepted)
            self.assertTrue(startup.enabled)
            self.assertTrue(controller.settings().first_run_completed)
            self.assertEqual(controller.settings().close_behavior, "background")

    def test_background_close_hides_window_and_quit_setting_closes_it(self):
        from paper_organizer.application.ai_settings import AiSettingsController
        from paper_organizer.application.library_workflow import LibraryWorkflowController
        from paper_organizer.application.lifecycle import LifecycleSettingsController
        from paper_organizer.application.summary_service import ImmediateSummaryController
        from paper_organizer.ui.main_window import PaperOrganizerWindow

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            secret_store = MemorySecretStore()
            startup = MemoryLoginStartup()
            lifecycle = LifecycleSettingsController(path, startup)
            lifecycle.save_preferences(
                start_with_windows=False,
                close_behavior="background",
            )
            window = PaperOrganizerWindow(
                AiSettingsController(secret_store, path),
                ImmediateSummaryController(secret_store, path),
                LibraryWorkflowController(path),
                lifecycle=lifecycle,
            )
            window.show()
            self.app.processEvents()

            with mock.patch(
                "paper_organizer.ui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
                return_value=True,
            ):
                self.assertFalse(window.close())
            self.assertFalse(window.isVisible())

            lifecycle.save_preferences(
                start_with_windows=False,
                close_behavior="quit",
            )
            window.show()
            self.app.processEvents()
            self.assertTrue(window.close())


if __name__ == "__main__":
    unittest.main()
