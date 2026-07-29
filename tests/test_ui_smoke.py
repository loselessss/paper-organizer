import importlib.util
import os
import tempfile
import time
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
        from paper_organizer.application.library_workflow import (
            EditablePaperMetadata,
            LibraryWorkflowController,
        )
        from paper_organizer.ui.ai_settings_dialog import AiSettingsDialog
        from paper_organizer.ui.main_window import PaperOrganizerWindow
        from paper_organizer.ui.ollama_model_dialog import OllamaModelDialog
        from paper_organizer.ui.ollama_model_dialog import _download_detail
        from paper_organizer.ui.startup_splash import CREATOR, create_splash

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = MemorySecretStore()
            ai_controller = AiSettingsController(store, path)
            workflow_controller = LibraryWorkflowController(path)
            with mock.patch.object(
                ai_controller,
                "installed_ollama_models",
                return_value=("qwen3:1.7b", "qwen3:4b"),
            ):
                dialog = AiSettingsDialog(ai_controller)
            model_dialog = OllamaModelDialog(ai_controller)
            window = PaperOrganizerWindow(ai_controller, workflow_controller)
            splash = create_splash()

            self.assertEqual(dialog.key_edit.echoMode(), QLineEdit.Password)
            self.assertEqual(dialog.windowTitle(), "요약 엔진 옵션")
            self.assertEqual(dialog.language_combo.currentData(), "ko")
            self.assertEqual(dialog.timeout_spin.value(), 900)
            self.assertIn("앱 1.6 요약 엔진 변경점", dialog.engine_changes_label.text())
            self.assertFalse(dialog.model_combo.isEditable())
            self.assertEqual(dialog.model_combo.count(), 2)
            dialog.model_combo.setCurrentIndex(
                dialog.model_combo.findData("qwen3:4b")
            )
            self.assertEqual(
                ai_controller.settings().selected_model,
                "qwen3:4b",
            )
            self.assertEqual(dialog.model_profile_combo.currentData(), "auto")
            self.assertEqual(
                dialog.manage_models_button.text(),
                "Ollama 설치·삭제…",
            )
            self.assertEqual(dialog.provider_group.title(), "제공자·출력")
            self.assertEqual(
                dialog.local_model_group.title(),
                "모델 선택·Ollama 설치 및 삭제",
            )
            self.assertGreaterEqual(dialog.minimumWidth(), 980)
            self.assertEqual(model_dialog.install_button.text(), "다운로드 후 선택")
            self.assertFalse(model_dialog.install_button.isEnabled())
            self.assertFalse(model_dialog.delete_button.isEnabled())
            self.assertGreaterEqual(model_dialog.minimumWidth(), 760)
            self.assertTrue(model_dialog.progress.alignment() & Qt.AlignLeft)
            model_dialog._worker = object()
            model_dialog._operation = "install"
            model_dialog._update_actions()
            self.assertTrue(model_dialog.cancel_button.isEnabled())
            model_dialog._worker = None
            model_dialog._operation = ""
            model_dialog._update_actions()
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
            self.assertNotIn("AI", menu_titles)
            settings_menu = next(
                action.menu()
                for action in window.menuBar().actions()
                if action.text() == "설정"
            )
            settings_categories = [
                action.text() for action in settings_menu.actions()
            ]
            self.assertEqual(
                settings_categories,
                ["요약 감시 옵션", "요약 엔진 옵션"],
            )
            checked = [
                action.text()
                for action in window._provider_group.actions()
                if action.isChecked()
            ]
            self.assertEqual(checked, ["로컬 Ollama"])
            from paper_organizer.ui.folder_settings_dialog import FolderSettingsDialog

            folder_dialog = FolderSettingsDialog(workflow_controller)
            self.assertEqual(folder_dialog.windowTitle(), "요약 감시 옵션")
            self.assertEqual(folder_dialog.watch_list.count(), 1)
            self.assertTrue(
                folder_dialog.watch_list.item(0).text().endswith("Downloads")
            )
            self.assertEqual(folder_dialog.interval_spin.value(), 300)
            self.assertFalse(folder_dialog.remove_source_check.isChecked())
            initial_categories = folder_dialog.focus_list.count()
            with mock.patch(
                "paper_organizer.ui.folder_settings_dialog.QInputDialog.getText",
                return_value=("사용자 정의 분야", True),
            ):
                folder_dialog._add_focus_category()
            self.assertEqual(
                folder_dialog.focus_list.count(),
                initial_categories + 1,
            )
            custom_item = folder_dialog.focus_list.currentItem()
            self.assertEqual(custom_item.text(), "사용자 정의 분야")
            with mock.patch(
                "paper_organizer.ui.folder_settings_dialog.QInputDialog.getText",
                return_value=("수정된 연구분야", True),
            ):
                folder_dialog._edit_focus_category()
            self.assertEqual(custom_item.text(), "수정된 연구분야")
            folder_dialog._remove_focus_categories()
            self.assertEqual(folder_dialog.focus_list.count(), initial_categories)
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
                "선택 항목 바로 분석",
            )
            self.assertEqual(
                window.collection_widget.organize_button.text(),
                "선택 항목 분석 큐로 보내기",
            )
            self.assertEqual(
                window.library_widget.save_button.text(),
                "색인 편집 저장 및 재색인",
            )
            self.assertEqual(
                window.library_widget.delete_button.text(),
                "선택 항목 완전 삭제",
            )
            self.assertTrue(window.queue_widget.table.acceptDrops())
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

    def test_natural_search_dialog_constructs_and_releases_runtime(self):
        from PyQt5.QtCore import Qt

        from paper_organizer.application.conversational_search import (
            SearchProviderView,
        )
        from paper_organizer.ui.search_chat_dialog import SearchChatDialog

        class FakeSearchController:
            def __init__(self):
                self.stop_calls = 0

            def provider_view(self):
                return SearchProviderView(
                    provider="ollama",
                    model="qwen3:4b",
                    sends_to_cloud=False,
                    requires_cloud_consent=False,
                )

            def stop_local_runtime(self):
                self.stop_calls += 1

        controller = FakeSearchController()
        dialog = SearchChatDialog(controller)

        self.assertEqual(dialog.windowTitle(), "자연어로 논문 찾기")
        self.assertFalse(
            bool(dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
        )
        self.assertIn("ollama", dialog.provider_label.text())
        self.assertFalse(dialog.answer_button.isEnabled())
        dialog.reject()
        self.assertEqual(controller.stop_calls, 1)

    def test_model_manager_opens_on_the_recommended_model(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QMessageBox

        from paper_organizer.application.ai_settings import AiSettingsController
        from paper_organizer.application.ollama_model_manager import (
            OllamaModelEntry,
            OllamaModelSnapshot,
        )
        from paper_organizer.infra.ollama_models import OllamaPullProgress
        from paper_organizer.ui.ollama_model_dialog import OllamaModelDialog

        with tempfile.TemporaryDirectory() as temp:
            controller = AiSettingsController(
                MemorySecretStore(),
                Path(temp) / "settings.json",
            )
            dialog = OllamaModelDialog(
                controller,
                initial_model="qwen3:8b",
            )
            snapshot = OllamaModelSnapshot(
                reachable=True,
                version="test",
                disk_path=temp,
                disk_free_gb=100,
                entries=(
                    OllamaModelEntry(
                        "qwen3:4b",
                        "Qwen3 4B",
                        2.5,
                        True,
                        2.5,
                        "4B",
                        "Q4_K_M",
                        True,
                    ),
                    OllamaModelEntry(
                        "qwen3:8b",
                        "Qwen3 8B",
                        5.2,
                        False,
                        0,
                        "",
                        "",
                        False,
                    ),
                    OllamaModelEntry(
                        "gemma3:12b",
                        "Gemma 3 12B",
                        None,
                        True,
                        8.1,
                        "12.2B",
                        "Q4_K_M",
                        False,
                        False,
                    ),
                ),
            )

            dialog._apply_snapshot(snapshot)

            self.assertEqual(dialog.model_combo.currentData(), "qwen3:8b")
            self.assertEqual(dialog.model_combo.findData("gemma3:12b"), -1)
            installed_text = "\n".join(
                dialog.installed_models.item(row).text()
                for row in range(dialog.installed_models.count())
            )
            self.assertIn("12B 이상 선택 제외", installed_text)
            self.assertEqual(dialog.install_button.text(), "다운로드 후 선택")
            dialog.installed_models.setCurrentRow(0)
            self.assertEqual(dialog.model_combo.currentData(), "qwen3:4b")
            self.assertTrue(dialog.delete_button.isEnabled())
            dialog.installed_models.setCurrentRow(1)
            self.assertTrue(dialog.delete_button.isEnabled())
            self.assertIn("분석 모델 선택 제외", dialog.model_detail.text())
            with (
                mock.patch.object(
                    QMessageBox,
                    "warning",
                    return_value=QMessageBox.Yes,
                ),
                mock.patch.object(dialog, "_start_worker") as start_worker,
            ):
                dialog._delete()
            start_worker.assert_called_once_with("delete", "gemma3:12b")
            dialog._progress_changed(OllamaPullProgress("pulling", 80, 100))
            self.assertEqual(dialog.progress.value(), 80)
            dialog._progress_changed(OllamaPullProgress("pulling", 10, 100))
            self.assertEqual(dialog.progress.value(), 80)
            refresh_flag_during_message = []
            dialog._operation = "delete"
            dialog._operation_model = "gemma3:12b"
            dialog._worker = object()
            with mock.patch.object(
                QMessageBox,
                "information",
                side_effect=lambda *_args: refresh_flag_during_message.append(
                    dialog._refresh_after_operation
                ),
            ):
                dialog._operation_completed(False)
            installed_after_delete = {
                dialog.installed_models.item(row).data(Qt.UserRole)
                for row in range(dialog.installed_models.count())
            }
            self.assertNotIn("gemma3:12b", installed_after_delete)
            self.assertEqual(refresh_flag_during_message, [True])
            self.assertEqual(dialog.progress.format(), "삭제 완료")
            dialog._worker = None
            dialog.close()

    def test_update_dialog_shows_the_versioned_installer_name(self):
        from PyQt5.QtWidgets import QLabel, QMessageBox

        from paper_organizer.application.update_service import (
            AvailableUpdate,
            GitHubUpdateService,
            ReleaseAsset,
        )
        from paper_organizer.ui.update_dialog import UpdateDialog

        asset = ReleaseAsset(
            name="PaperOrganizer_Setup_1.3.1.exe",
            download_url=(
                "https://github.com/loselessss/paper-organizer/releases/"
                "download/v1.3.1/PaperOrganizer_Setup_1.3.1.exe"
            ),
            size=128 * 1024 * 1024,
            sha256="0" * 64,
        )
        update = AvailableUpdate(
            version="1.3.1",
            tag_name="v1.3.1",
            release_name="Paper Organizer 1.3.1",
            release_notes="Update notes",
            release_url=(
                "https://github.com/loselessss/paper-organizer/releases/"
                "tag/v1.3.1"
            ),
            published_at="2026-07-26T00:00:00Z",
            asset=asset,
        )
        dialog = UpdateDialog(GitHubUpdateService("1.3.0"), update)

        labels = {label.text() for label in dialog.findChildren(QLabel)}
        self.assertIn(
            "PaperOrganizer_Setup_1.3.1.exe (128.0 MB)",
            labels,
        )
        skipped = []
        dialog.skip_requested.connect(skipped.append)
        with mock.patch.object(
            QMessageBox, "question", return_value=QMessageBox.Yes
        ):
            dialog._skip_version()
        self.assertEqual(skipped, ["1.3.1"])
        dialog.close()

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
        immediate_requests = []
        widget.immediate_analysis_requested.connect(immediate_requests.append)
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
        self.assertTrue(widget.table.dragEnabled())
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
        self.assertIn("2개를 분석 큐", widget.status_label.text())
        self.assertEqual(immediate_requests, [2])

        controller.organized.clear()
        with (
            mock.patch.object(QMessageBox, "question") as question,
            mock.patch.object(QMessageBox, "information") as information,
            mock.patch.object(widget, "scan_now"),
        ):
            widget.organize_dropped(
                [item.identity.file_sha256 for item in widget._items]
            )

        self.assertEqual(len(controller.organized), 2)
        question.assert_not_called()
        information.assert_not_called()
        self.assertEqual(immediate_requests, [2, 2])

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

    def test_exact_file_double_click_routes_to_existing_library_paperpack(self):
        from paper_organizer.application.library_workflow import (
            DuplicateReference,
            EditablePaperMetadata,
            ReviewItem,
        )
        from paper_organizer.models.paper import (
            DocumentIdentity,
            DuplicateKind,
            DuplicateMatch,
        )
        from paper_organizer.ui.library_workflow_widget import CollectionReviewWidget

        class FakeController:
            def settings(self):
                return type(
                    "Settings",
                    (),
                    {"scan_interval_seconds": 300, "auto_enabled": False},
                )()

        with tempfile.TemporaryDirectory() as temp:
            existing = Path(temp) / "existing.paperpack"
            existing.write_bytes(b"paperpack")
            incoming = Path(temp) / "incoming.pdf"
            identity = DocumentIdentity(
                file_id="sha256:" + "a" * 64,
                edition_id="sha256:" + "a" * 64,
                work_id="work:test",
                file_sha256="a" * 64,
                content_fingerprint="content:test",
                segment_fingerprints=(),
                fingerprint_version="v1",
                doi=None,
                source_variant="publisher",
                wrapper_pages=(),
                content_start_pdf_page=1,
                page_count=3,
            )
            duplicate = DuplicateReference(
                match=DuplicateMatch(
                    DuplicateKind.EXACT_FILE, 1.0, ("same hash",)
                ),
                title="Existing",
                pdf_path=Path(temp) / "materialized.pdf",
                sidecar_path=existing,
                source_variant="publisher",
            )
            widget = CollectionReviewWidget(FakeController())
            widget._items = [
                ReviewItem(
                    path=incoming,
                    identity=identity,
                    metadata=EditablePaperMetadata(title="Incoming"),
                    detection_status="academic_likely",
                    detection_reason="학술 문서",
                    duplicate=duplicate,
                )
            ]
            routed = []
            widget.library_requested.connect(routed.append)
            widget._open_row(0)
            self.assertEqual(routed, [str(existing)])
            widget.close()

    def test_immediate_analysis_runs_selected_items_without_polling_gap(self):
        from paper_organizer.application.background_analysis import AnalysisRunEvent
        from paper_organizer.ui.library_workflow_widget import (
            _BackgroundAnalysisWorker,
        )

        class FakeService:
            def __init__(self):
                self.calls = []

            def recover_interrupted(self):
                return 0

            def run_next(self, **kwargs):
                keep_runtime = kwargs["keep_runtime"]
                self.calls.append(
                    (
                        time.monotonic(),
                        kwargs["force"],
                        keep_runtime() if callable(keep_runtime) else keep_runtime,
                    )
                )
                return AnalysisRunEvent(
                    "completed",
                    "완료",
                    f"queue-{len(self.calls)}",
                    f"Paper {len(self.calls)}",
                )

            def poll_interval(self):
                return 10

        service = FakeService()
        worker = _BackgroundAnalysisWorker(service)
        worker.request_immediate(2)
        worker.start()
        deadline = time.monotonic() + 2
        while len(service.calls) < 2 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        worker.request_stop()
        self.assertTrue(worker.wait(2000))
        self.assertEqual(
            [force for _when, force, _keep_runtime in service.calls[:2]],
            [True, True],
        )
        self.assertEqual(
            [keep_runtime for _when, _force, keep_runtime in service.calls[:2]],
            [True, False],
        )
        self.assertLess(service.calls[1][0] - service.calls[0][0], 1)

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
        self.assertEqual(widget.table.rowCount(), 2)
        self.assertNotIn(
            "Alpha",
            {
                widget.table.item(row, 2).text()
                for row in range(widget.table.rowCount())
            },
        )

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
        for row in range(widget.table.rowCount()):
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

    def test_analysis_queue_double_click_routes_paperpack_to_library(self):
        from paper_organizer.application.analysis_queue import AnalysisQueueItem
        from paper_organizer.ui.library_workflow_widget import AnalysisQueueWidget

        with tempfile.TemporaryDirectory() as temp:
            paperpack = Path(temp) / "paper.paperpack"
            paperpack.write_bytes(b"placeholder")
            item = AnalysisQueueItem(
                queue_id="sha256:" + "b" * 64,
                path=str(paperpack),
                file_sha256="b" * 64,
                title="Queued",
                status="organized_pending_analysis",
                priority=0,
                added_at="2026-07-28T00:00:00+00:00",
                updated_at="2026-07-28T00:00:00+00:00",
            )

            class FakeController:
                def analysis_queue(self):
                    return [item]

            widget = AnalysisQueueWidget(FakeController())
            widget.table.selectRow(0)
            routed = []
            widget.library_requested.connect(routed.append)
            widget._show_selected_in_library()
            self.assertEqual(routed, [str(paperpack)])
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
        from PyQt5.QtCore import QItemSelectionModel
        from PyQt5.QtWidgets import QAbstractItemView, QMessageBox

        from paper_organizer.application.library_workflow import (
            EditablePaperMetadata,
            LibraryEntry,
        )
        from paper_organizer.ui.library_workflow_widget import (
            LibraryWidget,
            _analysis_version_label,
        )

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
                    "description": {"summary": "분석 요약"},
                    "analysis": {
                        "status": "completed",
                        "completed_at": "2026-07-28T12:00:00+09:00",
                        "provenance": {
                            "app_version": "1.4.1",
                            "provider": "ollama",
                            "model": "qwen3:8b",
                            "prompt_version": "paper-summary-v9-direct",
                        },
                    },
                },
            )
            second_pack = Path(temp) / "paper-two.paperpack"
            second_pack.write_bytes(b"placeholder")
            second = LibraryEntry(
                pdf_path=second_pack,
                sidecar_path=second_pack,
                metadata=EditablePaperMetadata(title="Second English Title"),
                work_id="work:second",
                source_variant="publisher",
                record={"description": {}, "analysis": {}},
            )

            class FakeLibraryController:
                def __init__(self):
                    self.search_queries = []
                    self.deleted = []

                def invalidate_library_cache(self):
                    pass

                def list_library(self):
                    return [
                        value
                        for value in (entry, second)
                        if value not in self.deleted
                    ]

                def search_library(self, query):
                    self.search_queries.append(query)
                    return self.list_library()

                def analysis_queue(self):
                    return []

                def paperpack_working_copy(self, _path):
                    return None

                def permanently_delete_library_entries(self, entries):
                    self.deleted.extend(entries)
                    return mock.Mock(deleted=len(entries), problems=())

            controller = FakeLibraryController()
            widget = LibraryWidget(controller)
            self.assertEqual(
                widget.table.selectionMode(),
                QAbstractItemView.ExtendedSelection,
            )
            self.assertTrue(widget.search_edit.isClearButtonEnabled())
            self.assertEqual(
                widget.search_edit.placeholderText(),
                "제목·저자·키워드 검색 · 자연어 질문 검색도 가능",
            )
            routed_queries = []
            widget.natural_search_requested.connect(routed_queries.append)
            widget.search_edit.setText("열에 강한 효소를 만든 논문은?")
            widget._submit_search()
            self.assertEqual(routed_queries, ["열에 강한 효소를 만든 논문은?"])
            self.assertEqual(controller.search_queries, [])
            widget.search_edit.setText("thermostable enzyme")
            widget._submit_search()
            self.assertEqual(controller.search_queries, ["thermostable enzyme"])
            self.assertIn("분석 요약", widget.analysis_view.toPlainText())
            self.assertEqual(
                widget.table.item(0, 5).text(),
                "분석 완료 (v1.4.1)",
            )
            self.assertIn("앱 v1.4.1", widget.analysis_view.toPlainText())
            self.assertEqual(
                _analysis_version_label(
                    {
                        "analysis": {
                            "provenance": {
                                "prompt_version": "paper-summary-v9-direct"
                            }
                        }
                    }
                ),
                "요약 v9",
            )
            entry.record["workflow"] = {"analysis_status": "failed"}
            entry.record["analysis"]["last_attempt"] = {
                "status": "failed",
                "error": "AI가 세 번 연속 올바른 JSON을 만들지 못했습니다.",
                "failed_at": "2026-07-29T12:34:56+09:00",
                "diagnostics": {
                    "stage": "summary_generation_and_validation",
                    "failure_kind": "json_validation",
                    "error_type": "SummaryRetryExhaustedError",
                    "provider": "ollama",
                    "model": "qwen3:4b",
                    "request_attempts": 3,
                    "summary_strategy": "hierarchical",
                    "output_language": "ko",
                    "included_sections": ["Abstract", "Results"],
                },
                "fallback": {
                    "source": "auto:regex",
                    "abstract": "Original abstract fallback.",
                    "abstract_pdf_pages": [1],
                    "facts": ["Year candidates: 2026"],
                },
            }
            widget.refresh(True)
            failed_text = widget.analysis_view.toPlainText()
            self.assertEqual(widget.table.item(0, 5).text(), "분석 실패")
            self.assertIn("AI 요약 실패", failed_text)
            self.assertIn("정규식 추출 Abstract", failed_text)
            self.assertIn("Original abstract fallback.", failed_text)
            self.assertIn("JSON 형식·스키마 검증 실패", failed_text)
            self.assertIn("요청 시도 횟수: 3회", failed_text)
            self.assertIn("ollama / qwen3:4b", failed_text)
            self.assertIn("Abstract, Results", failed_text)
            self.assertNotIn("분석 요약", failed_text)
            entry.record.pop("workflow")
            entry.record["analysis"].pop("last_attempt")
            widget.refresh(True)
            widget.table.selectionModel().select(
                widget.table.model().index(1, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
            self.assertEqual(len(widget._selected_entries()), 2)
            self.assertFalse(widget.form.isEnabled())
            widget.table.clearSelection()
            widget.table.selectRow(0)
            widget.search_edit.setText("다른 검색어")
            self.assertTrue(widget.select_path(paperpack))
            self.assertEqual(widget.search_edit.text(), "")
            self.assertIn("분석 요약", widget.analysis_view.toPlainText())
            widget.table.selectAll()
            with (
                mock.patch(
                    "paper_organizer.ui.library_workflow_widget.QMessageBox.question",
                    return_value=QMessageBox.Yes,
                ),
                mock.patch(
                    "paper_organizer.ui.library_workflow_widget.QMessageBox.warning"
                ) as warning,
            ):
                widget._delete_selected()
            self.assertEqual(len(controller.deleted), 2)
            self.assertEqual(widget.table.rowCount(), 0)
            warning.assert_not_called()
            widget.close()

    def test_background_close_hides_window_and_quit_setting_closes_it(self):
        from paper_organizer.application.ai_settings import AiSettingsController
        from paper_organizer.application.library_workflow import LibraryWorkflowController
        from paper_organizer.application.lifecycle import LifecycleSettingsController
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

    def test_second_gui_instance_notifies_the_first_instead_of_adding_a_tray(self):
        from uuid import uuid4

        from paper_organizer.ui.single_instance import SingleInstanceGuard

        server_name = f"paper-organizer-test-{uuid4().hex}"
        first = SingleInstanceGuard(server_name)
        second = SingleInstanceGuard(server_name)
        activated: list[bool] = []
        first.activation_requested.connect(lambda: activated.append(True))
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            for _ in range(10):
                self.app.processEvents()
                if activated:
                    break
                time.sleep(0.01)
            self.assertEqual(activated, [True])
        finally:
            second.close()
            first.close()


if __name__ == "__main__":
    unittest.main()
