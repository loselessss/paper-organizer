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
        from PyQt5.QtCore import QItemSelectionModel, Qt
        from PyQt5.QtWidgets import QAction, QMessageBox
        from PyQt5.QtWidgets import QLabel, QLineEdit

        from paper_organizer.application.ai_settings import AiSettingsController
        from paper_organizer import __version__
        from paper_organizer.application.summary_service import (
            ImmediateSummaryController,
        )
        from paper_organizer.application.library_workflow import (
            EditablePaperMetadata,
            LibraryWorkflowController,
        )
        from paper_organizer.ui.ai_settings_dialog import AiSettingsDialog
        from paper_organizer.ui.main_window import PaperOrganizerWindow
        from paper_organizer.ui.ollama_model_dialog import OllamaModelDialog
        from paper_organizer.ui.ollama_model_dialog import _download_detail
        from paper_organizer.ui.immediate_summary_widget import ImmediateSummaryDialog
        from paper_organizer.ui.startup_splash import CREATOR, create_splash

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = MemorySecretStore()
            ai_controller = AiSettingsController(store, path)
            summary_controller = ImmediateSummaryController(store, path)
            workflow_controller = LibraryWorkflowController(path)
            dialog = AiSettingsDialog(ai_controller)
            model_dialog = OllamaModelDialog(ai_controller)
            summary_dialog = ImmediateSummaryDialog(summary_controller)
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
            download_text = _download_detail(
                512 * 1024 * 1024,
                1024 * 1024 * 1024,
                16 * 1024 * 1024,
            )
            self.assertIn("16.0MB/s", download_text)
            self.assertIn("남음", download_text)
            self.assertFalse(
                bool(model_dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
            )
            self.assertFalse(
                bool(summary_dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
            )
            self.assertEqual(window.tabs.count(), 2)
            self.assertEqual(
                window.windowTitle(), f"Paper Organizer — v{__version__}"
            )
            window.collection_widget.form.set_metadata(
                EditablePaperMetadata(
                    title="Patent",
                    document_type="patent",
                    publication_number="US20260000001",
                )
            )
            self.assertEqual(
                window.collection_widget.form.authors_label.text(), "발명자"
            )
            self.assertTrue(window.collection_widget.form.venue_edit.isHidden())
            self.assertFalse(
                window.collection_widget.form.publication_number_edit.isHidden()
            )
            self.assertEqual(window.tabs.tabText(0), "수집 및 분석")
            self.assertEqual(window.tabs.tabText(1), "라이브러리")
            menu_titles = [
                action.text() for action in window.menuBar().actions()
            ]
            self.assertIn("도구", menu_titles)
            self.assertIn("AI", menu_titles)
            checked = [
                action.text()
                for action in window._provider_group.actions()
                if action.isChecked()
            ]
            self.assertEqual(checked, ["로컬 Ollama"])
            from paper_organizer.ui.folder_settings_dialog import FolderSettingsDialog

            folder_dialog = FolderSettingsDialog(workflow_controller)
            self.assertEqual(folder_dialog.watch_list.count(), 1)
            self.assertTrue(
                folder_dialog.watch_list.item(0).text().endswith("Downloads")
            )
            self.assertEqual(folder_dialog.interval_spin.value(), 300)
            self.assertFalse(folder_dialog.remove_source_check.isChecked())
            folder_dialog.close()
            self.assertEqual(
                window.library_widget.apply_pdf_button.text(),
                "편집본을 PaperPack에 적용",
            )
            self.assertEqual(
                window.library_widget._selected() is not None,
                bool(window.library_widget._entries),
            )
            self.assertEqual(
                window.collection_widget.form.venue_edit.placeholderText(),
                "저널명 또는 학회명",
            )
            self.assertEqual(CREATOR, "SANGKYU SHIN, Ph.D.")
            splash_labels = {label.text() for label in splash.findChildren(QLabel)}
            self.assertIn("Paper Organizer", splash_labels)
            self.assertIn(f"Version {__version__}", splash_labels)
            self.assertEqual(
                window.queue_widget.background_button.text(),
                "백그라운드 분석 시작",
            )
            self.assertEqual(
                window.queue_widget.run_now_button.text(),
                "선택 항목 지금 분석",
            )
            self.assertEqual(
                window.collection_widget.trash_button.text(),
                "제외 목록으로 보내기",
            )
            self.assertIn(
                "업데이트 확인...",
                {action.text() for action in window.findChildren(QAction)},
            )
            self.assertIn("Created by SANGKYU SHIN, Ph.D.", splash_labels)
            splash.close()
            model_dialog.close()
            dialog.close()
            window.close()

    def test_excluded_file_restore_dialog_uses_wide_multi_select_table(self):
        from PyQt5.QtCore import QItemSelectionModel

        from paper_organizer.application.library_workflow import TrashEntry
        from paper_organizer.ui.library_workflow_widget import TrashRestoreDialog

        entries = [
            TrashEntry(
                operation_id="one",
                manifest_path=Path("C:/trash/one/manifest.json"),
                original_path=Path("C:/papers/paper-one.pdf"),
                trashed_path=Path("C:/trash/one/paper-one.pdf"),
                duplicate_of=Path("C:/library/published.paperpack"),
                kind="unorganized_duplicate",
                detection_status="academic_likely",
                detection_reason="학술 문서 특징을 찾았습니다.",
                estimated_title="An Estimated Paper Title",
                duplicate_title="Published Paper",
                duplicate_kind="same_work",
                duplicate_score=0.97,
            ),
            TrashEntry(
                operation_id="two",
                manifest_path=Path("C:/trash/two/manifest.json"),
                original_path=Path("C:/papers/patent-two.pdf"),
                trashed_path=Path("C:/trash/two/patent-two.pdf"),
                duplicate_of=Path(),
                kind="discarded_new_pdf",
                detection_status="patent_likely",
                estimated_title="A Patent Title",
            ),
        ]
        dialog = TrashRestoreDialog(entries)
        self.assertGreaterEqual(dialog.minimumWidth(), 900)
        self.assertEqual(dialog.table.columnCount(), 4)
        self.assertEqual(
            [
                dialog.table.horizontalHeaderItem(column).text()
                for column in range(4)
            ],
            ["파일", "판정", "중복", "추정 제목"],
        )
        self.assertEqual(dialog.table.item(0, 1).text(), "학술 논문")
        self.assertEqual(
            dialog.table.item(0, 2).text(),
            "Published Paper · 같은 문헌 · 0.97",
        )
        self.assertEqual(dialog.table.item(1, 1).text(), "특허")
        self.assertEqual(dialog.table.item(1, 2).text(), "없음")
        dialog.table.selectionModel().select(
            dialog.table.model().index(1, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
        self.assertEqual(
            [entry.operation_id for entry in dialog.selected_entries()],
            ["one", "two"],
        )
        dialog.close()

    def test_collection_review_supports_batch_store_without_success_popup(self):
        from PyQt5.QtCore import QItemSelectionModel
        from PyQt5.QtWidgets import QAbstractItemView, QMessageBox, QTableWidgetItem

        from paper_organizer.application.library_workflow import (
            EditablePaperMetadata,
            OrganizedPaper,
            ReviewItem,
            TrashOperation,
        )
        from paper_organizer.models.paper import DocumentIdentity
        from paper_organizer.ui.library_workflow_widget import CollectionReviewWidget

        class FakeController:
            def __init__(self):
                self.organized = []
                self.trashed = []

            def settings(self):
                return type(
                    "Settings",
                    (),
                    {"scan_interval_seconds": 300, "auto_enabled": False},
                )()

            def suggest_metadata(self, item):
                return item.metadata

            def organize(self, item, metadata):
                self.organized.append((item, metadata))
                return OrganizedPaper(item.path, item.path)

            def trash_confirmed_duplicate(self, item):
                self.trashed.append(item)
                return TrashOperation("operation", item.path, item.path)

        def review_item(number):
            key = str(number) * 64
            identity = DocumentIdentity(
                file_id=f"sha256:{key}",
                edition_id=f"sha256:{key}",
                work_id=f"work:{number}",
                file_sha256=key,
                content_fingerprint=f"content:{number}",
                segment_fingerprints=(),
                fingerprint_version="v1",
                doi=None,
                source_variant="publisher",
                wrapper_pages=(),
                content_start_pdf_page=1,
                page_count=3,
            )
            return ReviewItem(
                path=Path(f"C:/papers/paper-{number}.pdf"),
                identity=identity,
                metadata=EditablePaperMetadata(title=f"Paper {number}"),
                detection_status="academic_likely",
                detection_reason="학술 문서 특징을 찾았습니다.",
            )

        controller = FakeController()
        widget = CollectionReviewWidget(controller)
        widget._items = [review_item(1), review_item(2)]
        widget.table.setRowCount(2)
        for row, item in enumerate(widget._items):
            widget.table.setItem(row, 0, QTableWidgetItem(item.path.name))
        widget.table.selectRow(0)
        widget.table.selectionModel().select(
            widget.table.model().index(1, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )

        self.assertEqual(
            widget.table.selectionMode(), QAbstractItemView.ExtendedSelection
        )
        self.assertEqual(len(widget._selected_items()), 2)
        self.assertFalse(widget.form.isEnabled())

        with (
            mock.patch.object(
                QMessageBox, "question", return_value=QMessageBox.Yes
            ),
            mock.patch.object(QMessageBox, "information") as information,
            mock.patch.object(widget, "scan_now"),
        ):
            widget._organize_selected()

        self.assertEqual(len(controller.organized), 2)
        information.assert_not_called()
        self.assertIn("2개를 보관", widget.status_label.text())

        with (
            mock.patch.object(
                QMessageBox, "question", return_value=QMessageBox.Yes
            ),
            mock.patch.object(QMessageBox, "information") as information,
            mock.patch.object(widget, "scan_now"),
        ):
            widget._trash_selected()

        self.assertEqual(len(controller.trashed), 2)
        information.assert_not_called()
        self.assertIn("2개를 제외 목록", widget.status_label.text())
        widget.close()

    def test_analysis_queue_sorting_keeps_selection_mapping(self):
        from PyQt5.QtCore import QItemSelectionModel, Qt
        from PyQt5.QtWidgets import QMessageBox

        from paper_organizer.application.analysis_queue import AnalysisQueueItem
        from paper_organizer.application.background_analysis import AnalysisRunEvent
        from paper_organizer.ui.library_workflow_widget import AnalysisQueueWidget

        def queue_item(key, title, priority, status):
            return AnalysisQueueItem(
                queue_id=f"sha256:{key}",
                path=f"C:/library/{key}.paperpack",
                file_sha256=key,
                title=title,
                status=status,
                priority=priority,
                added_at="2026-07-23T00:00:00+00:00",
                updated_at="2026-07-23T00:00:00+00:00",
            )

        class FakeController:
            def __init__(self, items):
                self._items = items
                self.removed = []

            def analysis_queue(self):
                return self._items

            def remove_from_queue(self, queue_id):
                self.removed.append(queue_id)
                self._items = [
                    item for item in self._items if item.queue_id != queue_id
                ]

        items = [
            queue_item("a", "Alpha", 0, "completed"),
            queue_item("b", "Beta", 1, "organized_pending_analysis"),
            queue_item("c", "Gamma", 0, "failed"),
        ]
        widget = AnalysisQueueWidget(FakeController(items))

        widget.table.sortItems(2, Qt.DescendingOrder)
        widget.table.selectRow(0)
        self.assertEqual(widget.table.item(0, 2).text(), "Gamma")
        self.assertEqual(widget._selected().queue_id, "sha256:c")

        widget.table.sortItems(0, Qt.AscendingOrder)
        self.assertEqual(widget.table.item(0, 0).text(), "높음")
        widget.table.selectRow(0)
        self.assertEqual(widget._selected().queue_id, "sha256:b")

        widget.refresh()
        self.assertEqual(widget._selected().queue_id, "sha256:b")
        library_events = []
        widget.library_changed.connect(lambda: library_events.append(True))
        widget._analysis_event(
            AnalysisRunEvent("completed", "저장 완료", "sha256:a", "Alpha")
        )
        self.assertEqual(library_events, [True])
        selection = widget.table.selectionModel()
        for row in (0, 1):
            selection.select(
                widget.table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        with mock.patch.object(
            QMessageBox, "question", return_value=QMessageBox.Yes
        ):
            widget._remove_selected()
        self.assertEqual(len(widget._controller.removed), 2)
        widget.close()

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

    def test_selecting_same_library_path_renders_analysis_immediately(self):
        from paper_organizer.application.library_workflow import (
            EditablePaperMetadata,
            LibraryEntry,
        )
        from paper_organizer.ui.library_workflow_widget import LibraryWidget

        with tempfile.TemporaryDirectory() as temp:
            paperpack = Path(temp) / "paper.paperpack"
            paperpack.write_bytes(b"placeholder")
            entry = LibraryEntry(
                pdf_path=paperpack,
                sidecar_path=paperpack,
                metadata=EditablePaperMetadata(title="Original English Title"),
                work_id="work:test",
                source_variant="publisher",
                record={
                    "description": {"summary_ko": "분석 요약"},
                    "analysis": {"status": "completed"},
                },
            )

            class FakeLibraryController:
                def invalidate_library_cache(self):
                    pass

                def list_library(self):
                    return [entry]

                def analysis_queue(self):
                    return []

                def paperpack_working_copy(self, _path):
                    return None

            widget = LibraryWidget(FakeLibraryController())
            self.assertTrue(widget.search_edit.isClearButtonEnabled())
            self.assertIn("분석 요약", widget.analysis_view.toPlainText())
            widget.search_edit.setText("다른 검색어")
            self.assertTrue(widget.select_path(paperpack))
            self.assertEqual(widget.search_edit.text(), "")
            self.assertIn("분석 요약", widget.analysis_view.toPlainText())
            widget.close()

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
