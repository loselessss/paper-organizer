import importlib.util
import os
import tempfile
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
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

    def test_fluent_choice_controls_keep_native_indicators(self):
        from paper_organizer.ui.fluent_style import apply_fluent_theme

        apply_fluent_theme(self.app)
        stylesheet = self.app.styleSheet()

        self.assertNotIn("QComboBox::down-arrow", stylesheet)
        self.assertNotIn("QSpinBox::up-arrow", stylesheet)
        self.assertNotIn("QSpinBox::down-arrow", stylesheet)
        self.assertNotIn("border-top: 5px solid #5f5f5f", stylesheet)
        self.assertNotIn("border-bottom: 4px solid #6f6f6f", stylesheet)
        self.assertNotIn("QComboBox,\n        QSpinBox", stylesheet)
        self.assertNotIn("QComboBox:focus", stylesheet)
        self.assertNotIn("QSpinBox:focus", stylesheet)
        self.assertIn("QCheckBox {\n            background: transparent;", stylesheet)

    def test_selection_ai_uses_a_separate_dialog(self):
        from PyQt5.QtCore import Qt

        from paper_organizer.integrations.spdf_bridge import SpdfSelection
        from paper_organizer.ui.library_workflow_widget import SelectionAiDialog

        dialog = SelectionAiDialog()
        self.assertFalse(dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
        selection = SpdfSelection(
            text="Selected enzyme activity paragraph.",
            pdf_page=3,
            bounding_boxes=((10.0, 20.0, 30.0, 40.0),),
            document_id="paper-1",
            document_path=Path("paper.pdf"),
        )
        requested = []
        dialog.action_requested.connect(requested.append)

        dialog.set_selection(selection, service_available=True)
        self.assertEqual(
            dialog.selection_preview.toPlainText(), selection.text
        )
        self.assertIn("PDF 3쪽", dialog.selection_label.text())
        self.assertTrue(dialog.translate_button.isEnabled())
        dialog.summary_button.click()
        self.assertEqual(requested, ["summarize"])
        dialog.show_result("선택 영역 요약 결과")
        self.assertEqual(dialog.result_view.toPlainText(), "선택 영역 요약 결과")
        self.assertTrue(dialog.copy_button.isEnabled())
        dialog.close()

    def test_claim_display_joins_soft_wraps_and_keeps_claim_boundaries(self):
        from paper_organizer.ui.library_workflow_widget import (
            _format_claims_for_display,
        )

        displayed = _format_claims_for_display(
            "청구범위\n"
            "1. 효소 복합체를 포함하고\n"
            "담체를 더 포함하는 조성물.\n"
            "2. 제1항에 있어서,\n"
            "상기 담체가 고분자인 조성물."
        )

        self.assertEqual(
            displayed,
            "청구범위\n\n"
            "1. 효소 복합체를 포함하고 담체를 더 포함하는 조성물.\n\n"
            "2. 제1항에 있어서, 상기 담체가 고분자인 조성물.",
        )

    def test_analysis_version_label_recognizes_split_paper_prompt_versions(self):
        from paper_organizer.ui.library_workflow_widget import (
            _analysis_version_label,
        )

        self.assertEqual(
            _analysis_version_label(
                {
                    "analysis": {
                        "provenance": {
                            "prompt_version": "review-summary-v5-direct",
                        }
                    }
                }
            ),
            "v5",
        )
        self.assertEqual(
            _analysis_version_label(
                {
                    "analysis": {
                        "provenance": {
                            "prompt_version": "research-summary-v11-direct",
                        }
                    }
                }
            ),
            "v11",
        )
        self.assertEqual(
            _analysis_version_label(
                {
                    "analysis": {
                        "provenance": {
                            "prompt_version": "paper-summary-v9-direct",
                        }
                    }
                }
            ),
            "v9",
        )

    def test_bibliography_reverify_is_hidden_after_automatic_checking(self):
        from paper_organizer.ui.library_workflow_widget import (
            _should_show_bibliography_reverify,
        )

        def entry(record, document_type="research_paper"):
            return SimpleNamespace(
                record=record,
                metadata=SimpleNamespace(document_type=document_type),
            )

        self.assertFalse(
            _should_show_bibliography_reverify(
                entry(
                    {
                        "analysis": {
                            "provenance": {
                                "app_version": "2.3.0",
                                "provider": "ollama",
                            }
                        }
                    }
                )
            )
        )
        self.assertFalse(
            _should_show_bibliography_reverify(
                entry(
                    {
                        "curation": {
                            "field_sources": {
                                "bibliography.title": "verified:crossref"
                            }
                        }
                    }
                )
            )
        )
        self.assertTrue(
            _should_show_bibliography_reverify(
                entry(
                    {
                        "analysis": {
                            "provenance": {
                                "app_version": "2.2.0",
                                "provider": "ollama",
                            }
                        }
                    }
                )
            )
        )
        self.assertFalse(
            _should_show_bibliography_reverify(entry({}, document_type="patent"))
        )

    def test_ai_settings_and_summary_shell_construct(self):
        from PyQt5.QtCore import QItemSelectionModel, Qt
        from PyQt5.QtWidgets import (
            QAction,
            QBoxLayout,
            QFormLayout,
            QFrame,
            QMessageBox,
            QToolBar,
            QToolButton,
        )
        from PyQt5.QtWidgets import QLabel, QLineEdit, QPlainTextEdit, QWidget

        from paper_organizer.application.ai_settings import AiSettingsController
        from paper_organizer import __version__
        from paper_organizer.application.lifecycle import LifecycleSettingsController
        from paper_organizer.application.library_workflow import (
            EditablePaperMetadata,
            LibraryWorkflowController,
        )
        from paper_organizer.ui.ai_settings_dialog import AiSettingsDialog
        from paper_organizer.ui.embedded_model_dialog import EmbeddedModelDialog
        from paper_organizer.ui.main_window import PaperOrganizerWindow
        from paper_organizer.ui.ollama_model_dialog import OllamaModelDialog
        from paper_organizer.ui.ollama_model_dialog import _download_detail
        from paper_organizer.ui.ollama_model_dialog import (
            _igpu_cpu_fallback_warning,
        )
        from paper_organizer.ui.startup_splash import CREATOR, create_splash

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = MemorySecretStore()
            ai_controller = AiSettingsController(store, path)
            workflow_controller = LibraryWorkflowController(path)
            lifecycle_controller = LifecycleSettingsController(
                path,
                MemoryLoginStartup(),
            )
            with mock.patch.object(
                ai_controller,
                "installed_ollama_models",
                return_value=("qwen3:1.7b", "qwen3:4b"),
            ):
                dialog = AiSettingsDialog(ai_controller)
            embedded_model_dialog = EmbeddedModelDialog(ai_controller)
            model_dialog = OllamaModelDialog(ai_controller)
            window = PaperOrganizerWindow(
                ai_controller,
                workflow_controller,
                lifecycle=lifecycle_controller,
            )
            splash = create_splash()

            self.assertEqual(dialog.key_edit.echoMode(), QLineEdit.Password)
            self.assertFalse(
                bool(dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
            )
            self.assertEqual(
                dialog.windowTitle(),
                "요약 엔진 옵션 · 실험논문 v11 · 리뷰논문 v5 · 특허 v2",
            )
            self.assertEqual(dialog.language_combo.currentData(), "ko")
            self.assertEqual(dialog.timeout_spin.value(), 900)
            self.assertTrue(dialog.model_combo.isEditable())
            self.assertEqual(dialog.model_combo.count(), 1)
            self.assertEqual(dialog.manual_model_combo.count(), 1)
            self.assertEqual(ai_controller.settings().selected_model, "")
            self.assertIn("GGUF", dialog.model_status.text())
            self.assertIn("모두 선택", dialog.model_guidance.text())
            dialog._hardware_scan_completed(
                SimpleNamespace(
                    hardware=SimpleNamespace(
                        cpu_model="Test CPU",
                        logical_cores=12,
                        memory_available_gb=4.5,
                        memory_total_gb=16,
                        gpus=(),
                        model_disk_free_gb=91,
                    ),
                    ollama=SimpleNamespace(
                        reachable=True,
                        version="0.test",
                        models=(object(),),
                        running_models=(),
                    ),
                    local_model_count=0,
                    local_model_dir="C:/Paper Organizer/models",
                    recommendation=SimpleNamespace(
                        profile="auto",
                        recommended=None,
                    ),
                )
            )
            self.assertIn("앱 모델 0개", dialog.hardware_status.text())
            self.assertNotIn("Ollama", dialog.hardware_status.text())
            self.assertEqual(dialog.model_profile_combo.currentData(), "auto")
            with mock.patch.object(dialog, "_scan_hardware") as scan_hardware:
                dialog.model_profile_combo.setCurrentIndex(
                    dialog.model_profile_combo.findData("balanced")
                )
                scan_hardware.assert_called_once_with()
            self.assertFalse(dialog.background_resident_check.isChecked())
            self.assertIn("체크 안 됨", dialog.residency_guidance.text())
            self.assertIn("수동 요약 모델은 항상", dialog.residency_guidance.text())
            self.assertTrue(dialog.force_igpu_check.isChecked())
            self.assertIn("1.7B", dialog.igpu_guidance.text())
            self.assertIn("GPU 사용을 보장하지 않으며", dialog.igpu_guidance.text())
            self.assertEqual(
                dialog.manage_models_button.text(),
                "모델 다운로드·관리…",
            )
            self.assertEqual(
                embedded_model_dialog.windowTitle(),
                "내장 로컬 AI 모델 관리",
            )
            self.assertFalse(embedded_model_dialog.select_button.isEnabled())
            self.assertFalse(embedded_model_dialog.download_button.isEnabled())
            self.assertFalse(embedded_model_dialog.delete_button.isEnabled())
            self.assertGreaterEqual(embedded_model_dialog.minimumWidth(), 760)
            self.assertFalse(
                bool(
                    embedded_model_dialog.windowFlags()
                    & Qt.WindowContextHelpButtonHint
                )
            )
            self.assertEqual(dialog.provider_group.title(), "제공자·출력")
            self.assertEqual(
                dialog.local_model_group.title(),
                "추천·모델 다운로드·모델 선택",
            )
            local_labels = []
            for row in range(dialog.local_model_form.rowCount()):
                item = dialog.local_model_form.itemAt(row, QFormLayout.LabelRole)
                widget = item.widget() if item is not None else None
                if isinstance(widget, QLabel):
                    local_labels.append(widget.text())
            self.assertLess(
                local_labels.index("추천 프로필"),
                local_labels.index("로컬 모델"),
            )
            self.assertLess(
                local_labels.index("로컬 모델"),
                local_labels.index("백그라운드 모델"),
            )
            self.assertFalse(hasattr(dialog, "model_candidates"))
            self.assertEqual(
                dialog.local_model_group.findChildren(QPlainTextEdit),
                [],
            )
            self.assertLessEqual(dialog.minimumWidth(), 620)
            self.assertLessEqual(window.minimumSizeHint().width(), 1400)
            self.assertTrue(dialog.scroll_area.widgetResizable())
            dialog.resize(800, 700)
            dialog._update_responsive_layout()
            self.assertEqual(
                dialog.engine_columns.direction(),
                QBoxLayout.TopToBottom,
            )
            dialog.resize(1120, 700)
            dialog._update_responsive_layout()
            self.assertEqual(
                dialog.engine_columns.direction(),
                QBoxLayout.LeftToRight,
            )
            self.assertEqual(model_dialog.install_button.text(), "다운로드 후 선택")
            self.assertFalse(model_dialog.install_button.isEnabled())
            self.assertFalse(model_dialog.delete_button.isEnabled())
            self.assertGreaterEqual(model_dialog.minimumWidth(), 760)
            self.assertTrue(model_dialog.progress.alignment() & Qt.AlignCenter)
            self.assertEqual(
                model_dialog.layout().contentsMargins().left(),
                18,
            )
            self.assertEqual(
                model_dialog.layout().contentsMargins().right(),
                18,
            )
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
            self.assertIn(
                "현재 모델은 CPU로 실행 중",
                _igpu_cpu_fallback_warning(True, "CPU"),
            )
            self.assertEqual(_igpu_cpu_fallback_warning(False, "CPU"), "")
            self.assertEqual(_igpu_cpu_fallback_warning(True, "GPU"), "")
            self.assertFalse(
                bool(model_dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
            )
            self.assertEqual(
                window.windowTitle(), f"Paper Organizer — v{__version__}"
            )
            self.assertIs(window.centralWidget(), window.library_widget)
            self.assertTrue(window.menuBar().isHidden())
            new_pdf_popup = window.findChild(QFrame, "newPdfReviewPopup")
            self.assertIsNotNone(new_pdf_popup)
            self.assertFalse(new_pdf_popup.isVisible())
            analysis_popup = window.findChild(QFrame, "analysisQueuePopup")
            self.assertIsNotNone(analysis_popup)
            self.assertFalse(analysis_popup.isVisible())
            self.assertIsNot(new_pdf_popup, analysis_popup)
            ribbon = window.findChild(QToolBar, "commandRibbon")
            self.assertIsNotNone(ribbon)
            self.assertEqual(ribbon.toolButtonStyle(), Qt.ToolButtonTextUnderIcon)
            ribbon_actions = [
                action.text()
                for action in ribbon.actions()
                if not action.isSeparator() and action.text()
            ]
            self.assertEqual(
                ribbon_actions,
                [
                    "새 PDF",
                    "분석 큐",
                    "감시 설정",
                    "AI 설정",
                    "업데이트",
                    "단축키",
                    "정보",
                ],
            )
            ribbon_menus = [
                button.text()
                for button in ribbon.findChildren(QToolButton)
                if button.menu() is not None
            ]
            self.assertEqual(
                ribbon_menus,
                ["PDF 열기", "AI 번역", "PaperPack", "삭제", "재요약"],
            )
            reanalysis_menu = next(
                button.menu()
                for button in ribbon.findChildren(QToolButton)
                if button.text() == "재요약"
            )
            self.assertEqual(
                [
                    action.text()
                    for action in reanalysis_menu.actions()
                    if not action.isSeparator()
                ],
                ["선택 재요약", "전체 재요약"],
            )
            paperpack_menu = next(
                button.menu()
                for button in ribbon.findChildren(QToolButton)
                if button.text() == "PaperPack"
            )
            paperpack_visible_actions = [
                action.text()
                for action in paperpack_menu.actions()
                if action.isVisible() and not action.isSeparator()
            ]
            self.assertEqual(
                paperpack_visible_actions,
                ["적용", "편집 취소", "PDF 환원…", "검색 색인 재구축"],
            )
            with mock.patch.object(
                workflow_controller,
                "legacy_migration_preview",
                return_value=SimpleNamespace(candidates=(object(), object())),
            ):
                window._check_legacy_migration_candidates()
            self.assertIn("구버전 PaperPack 2개", window.statusBar().currentMessage())
            paperpack_visible_actions = [
                action.text()
                for action in paperpack_menu.actions()
                if action.isVisible() and not action.isSeparator()
            ]
            self.assertIn("고급", paperpack_visible_actions)
            self.assertTrue(
                all(
                    not action.icon().isNull()
                    for action in ribbon.actions()
                    if not action.isSeparator() and action.text()
                )
            )
            self.assertIs(new_pdf_popup.parent(), window)
            self.assertIs(analysis_popup.parent(), window)
            self.assertFalse(bool(new_pdf_popup.windowFlags() & Qt.Tool))
            self.assertFalse(bool(analysis_popup.windowFlags() & Qt.Tool))
            window.show()
            self.app.processEvents()
            stable_size = window.size()
            window._analysis_progress_changed("분석 큐 대기 1 · 실패 0", True)
            self.app.processEvents()
            self.assertEqual(window.size(), stable_size)
            self.assertEqual(window._analysis_progress_bar.minimum(), 0)
            self.assertEqual(window._analysis_progress_bar.maximum(), 0)
            window._analysis_progress_changed("분석 큐 대기 0 · 실패 0", False)
            self.app.processEvents()
            self.assertEqual(window.size(), stable_size)
            self.assertEqual(window._analysis_progress_bar.minimum(), 0)
            self.assertEqual(window._analysis_progress_bar.maximum(), 1)
            self.assertEqual(window._analysis_progress_bar.maximumWidth(), 0)
            window.toggle_analysis_queue()
            self.app.processEvents()
            self.assertEqual(window.size(), stable_size)
            self.assertTrue(analysis_popup.isVisible())
            self.assertFalse(new_pdf_popup.isVisible())
            self.assertLessEqual(
                analysis_popup.geometry().bottom(),
                window.statusBar().geometry().top(),
            )
            window.toggle_analysis_queue()
            self.app.processEvents()
            self.assertFalse(analysis_popup.isVisible())
            with mock.patch.object(
                window.collection_widget, "scan_now"
            ) as scan_now:
                window.show_new_pdf_review()
                self.app.processEvents()
                self.assertEqual(window.size(), stable_size)
                self.assertTrue(new_pdf_popup.isVisible())
                self.assertFalse(analysis_popup.isVisible())
                scan_now.assert_not_called()
                self.assertLessEqual(
                    new_pdf_popup.geometry().bottom(),
                    window.statusBar().geometry().top(),
                )
                window.show_new_pdf_review()
                self.app.processEvents()
                self.assertFalse(new_pdf_popup.isVisible())
                scan_now.assert_not_called()
            window.toggle_analysis_queue()
            self.app.processEvents()
            self.assertTrue(analysis_popup.isVisible())
            self.assertFalse(new_pdf_popup.isVisible())
            window.hide()
            self.app.processEvents()
            self.assertFalse(analysis_popup.isVisible())
            self.assertFalse(new_pdf_popup.isVisible())
            with (
                mock.patch.object(window.queue_widget, "refresh") as refresh_queue,
                mock.patch.object(
                    window.queue_widget, "start_background_analysis"
                ) as start_analysis,
            ):
                window._library_reanalysis_queued(3)
                refresh_queue.assert_called_once_with()
                start_analysis.assert_called_once_with(immediate_count=3)
            self.assertEqual(
                window._automatic_update_timer.interval(),
                24 * 60 * 60 * 1000,
            )
            self.assertFalse(window._automatic_update_timer.isActive())
            with mock.patch.object(
                window._update_schedule,
                "mark_checked",
            ) as mark_checked:
                window._update_check_failed("network unavailable", False)
                window._update_check_finished()
                mark_checked.assert_not_called()
                window._update_check_completed(None, False)
                mark_checked.assert_called_once_with()
            with (
                mock.patch(
                    "paper_organizer.ui.main_window.QMessageBox.information"
                ),
                mock.patch(
                    "paper_organizer.ui.main_window.FolderSettingsDialog"
                ) as first_run_watch,
                mock.patch(
                    "paper_organizer.ui.main_window.AiSettingsDialog"
                ) as first_run_engine,
                mock.patch(
                    "paper_organizer.ui.main_window.EmbeddedModelDialog"
                ),
            ):
                window.show_first_run_ai_setup()
                first_run_watch.assert_called_once_with(
                    workflow_controller,
                    window,
                    lifecycle=lifecycle_controller,
                )
                first_run_engine.assert_called_once_with(
                    ai_controller,
                    window,
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
            self.assertEqual(
                window.collection_widget.form.publication_number_label.text(),
                "출원/등록번호",
            )
            self.assertTrue(
                window.collection_widget.form.application_number_edit.isHidden()
            )
            checked = [
                action.text()
                for action in window._provider_group.actions()
                if action.isChecked()
            ]
            self.assertEqual(checked, ["내장 로컬 AI"])
            from paper_organizer.ui.folder_settings_dialog import FolderSettingsDialog

            folder_dialog = FolderSettingsDialog(workflow_controller)
            self.assertEqual(folder_dialog.windowTitle(), "요약 감시 옵션")
            self.assertFalse(
                bool(
                    folder_dialog.windowFlags()
                    & Qt.WindowContextHelpButtonHint
                )
            )
            content_layout = folder_dialog.layout().itemAt(0).layout()
            self.assertEqual(content_layout.stretch(0), 2)
            self.assertEqual(content_layout.stretch(1), 3)
            research_panel = folder_dialog.findChild(
                QWidget,
                "researchCategoriesPanel",
            )
            self.assertIsNotNone(research_panel)
            self.assertGreaterEqual(research_panel.minimumWidth(), 500)
            self.assertEqual(folder_dialog.watch_list.count(), 1)
            self.assertTrue(
                folder_dialog.watch_list.item(0).text().endswith("Downloads")
            )
            self.assertEqual(folder_dialog.interval_spin.value(), 300)
            self.assertFalse(folder_dialog.watch_subdirectories_check.isChecked())
            self.assertFalse(folder_dialog.remove_source_check.isChecked())
            self.assertEqual(folder_dialog.focus_list_label.text(), "분야 선택 ↓")
            self.assertEqual(folder_dialog.subcategory_list_label.text(), "세부분야")
            self.assertGreaterEqual(folder_dialog.focus_list.minimumWidth(), 260)
            self.assertGreaterEqual(
                folder_dialog.subcategory_list.minimumWidth(),
                200,
            )
            initial_categories = folder_dialog.focus_list.count()
            bioengineering_items = folder_dialog.focus_list.findItems(
                "생물공학",
                Qt.MatchExactly,
            )
            self.assertEqual(len(bioengineering_items), 1)
            folder_dialog.focus_list.setCurrentItem(bioengineering_items[0])
            subcategories = [
                folder_dialog.subcategory_list.item(row).text()
                for row in range(folder_dialog.subcategory_list.count())
            ]
            self.assertIn("단백질공학", subcategories)
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
            self.assertEqual(
                folder_dialog.subcategory_list.item(0).text(),
                "세부분야 없음",
            )
            with mock.patch(
                "paper_organizer.ui.folder_settings_dialog.QInputDialog.getText",
                return_value=("맞춤 세부분야", True),
            ):
                folder_dialog._add_subcategory()
            self.assertEqual(
                folder_dialog.subcategory_list.item(0).text(),
                "맞춤 세부분야",
            )
            with mock.patch(
                "paper_organizer.ui.folder_settings_dialog.QInputDialog.getText",
                return_value=("수정된 연구분야", True),
            ):
                folder_dialog._edit_focus_category()
            self.assertEqual(custom_item.text(), "수정된 연구분야")
            self.assertEqual(
                folder_dialog.subcategory_list.item(0).text(),
                "맞춤 세부분야",
            )
            folder_dialog.subcategory_list.item(0).setSelected(True)
            folder_dialog._remove_subcategories()
            self.assertEqual(
                folder_dialog.subcategory_list.item(0).text(),
                "세부분야 없음",
            )
            folder_dialog._remove_focus_categories()
            self.assertEqual(folder_dialog.focus_list.count(), initial_categories)
            folder_dialog.close()
            self.assertEqual(
                window.library_widget.apply_pdf_button.text(),
                "적용",
            )
            self.assertIn(
                "PaperPack",
                window.library_widget.apply_pdf_button.toolTip(),
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
                window.queue_widget.immediate_stop_button.text(),
                "즉시 정지",
            )
            self.assertFalse(window.queue_widget.immediate_stop_button.isEnabled())
            self.assertEqual(
                window.queue_widget.run_now_button.text(),
                "선택 항목 바로 분석",
            )
            self.assertEqual(
                window.collection_widget.organize_button.text(),
                "선택 항목 분석 큐로 보내기",
            )
            self.assertFalse(hasattr(window.collection_widget, "settings_button"))
            self.assertTrue(window.collection_widget.form.isHidden())
            self.assertEqual(window.collection_widget.edit_button.text(), "색인 수정…")
            self.assertFalse(window.collection_widget.scan_button.icon().isNull())
            self.assertEqual(
                window.collection_widget.scan_button.property("fluentRole"),
                "primary",
            )
            self.assertFalse(
                window.collection_widget.delete_pdf_button.icon().isNull()
            )
            self.assertEqual(
                window.collection_widget.delete_pdf_button.property("fluentRole"),
                "destructive",
            )
            self.assertFalse(window.queue_widget.run_now_button.icon().isNull())
            self.assertEqual(
                window.queue_widget.run_now_button.property("fluentRole"),
                "primary",
            )
            self.assertEqual(
                window.library_widget.save_button.text(),
                "색인 편집 저장 및 재색인",
            )
            self.assertFalse(window.library_widget.save_button.icon().isNull())
            self.assertEqual(
                window.library_widget.save_button.property("fluentRole"),
                "primary",
            )
            self.assertEqual(
                window.library_widget.delete_button.text(),
                "휴지통",
            )
            self.assertIn(
                "앱 휴지통",
                window.library_widget.delete_button.toolTip(),
            )
            self.assertFalse(
                window.library_widget.permanent_delete_button.icon().isNull()
            )
            self.assertEqual(
                window.library_widget.permanent_delete_button.property("fluentRole"),
                "destructive",
            )
            self.assertTrue(window.queue_widget.table.acceptDrops())
            self.assertEqual(
                window.collection_widget.trash_button.text(),
                "제외 목록으로 보내기",
            )
            self.assertIn(
                "업데이트",
                {action.text() for action in window.findChildren(QAction)},
            )
            self.assertIn("Created by SANGKYU SHIN, Ph.D.", splash_labels)
            creator_label = splash.findChild(QLabel, "splashCreatorLabel")
            self.assertIsNotNone(creator_label)
            self.assertGreaterEqual(creator_label.geometry().left(), 400)
            self.assertGreaterEqual(creator_label.geometry().top(), 390)
            self.assertIn("font-size: 8pt", creator_label.styleSheet())
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
        self.assertEqual(dialog.stop_button.text(), "정지")
        self.assertFalse(dialog.stop_button.icon().isNull())
        self.assertFalse(dialog.stop_button.isEnabled())
        dialog._set_busy(True, "검색 중")
        self.assertTrue(dialog.stop_button.isEnabled())
        dialog._set_busy(False, "대기")
        self.assertFalse(dialog.stop_button.isEnabled())
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
                initial_model="qwen3.5:4b",
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
                        "qwen3.5:4b",
                        "Qwen3.5 4B",
                        3.4,
                        False,
                        0,
                        "",
                        "",
                        False,
                        benchmark_summary=(
                            "★ 종합 추천 1순위\n"
                            "실논문 6/6편 완료 · 품질 33.92/100\n"
                            "강점: 연구·리뷰·서지 정확도"
                        ),
                        recommendation_rank=1,
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

            self.assertEqual(dialog.model_table.columnCount(), 6)
            self.assertEqual(dialog.model_table.rowCount(), 3)
            self.assertEqual(dialog._selected_entry().model_id, "qwen3.5:4b")
            recommended_row = dialog._row_for_model("qwen3.5:4b")
            self.assertEqual(
                dialog.model_table.item(recommended_row, 0).text(),
                "★ 1",
            )
            self.assertIn("수동 본문 분석 기본", dialog.model_detail.text())
            self.assertNotIn("품질 33.92/100", dialog.model_detail.text())
            self.assertNotIn("연구·리뷰·서지 정확도", dialog.model_detail.text())
            self.assertGreaterEqual(dialog.model_table.minimumHeight(), 260)
            gemma_row = dialog._row_for_model("gemma3:12b")
            self.assertGreaterEqual(gemma_row, 0)
            self.assertIn(
                "선택 제외",
                dialog.model_table.item(gemma_row, 2).text(),
            )
            self.assertEqual(dialog.install_button.text(), "다운로드 후 선택")
            qwen_row = dialog._row_for_model("qwen3:4b")
            dialog.model_table.setCurrentCell(qwen_row, 0)
            dialog.model_table.selectRow(qwen_row)
            self.assertEqual(dialog._selected_entry().model_id, "qwen3:4b")
            self.assertIn("수동 정밀 3~4B급", dialog.model_detail.text())
            self.assertTrue(dialog.delete_button.isEnabled())
            dialog.model_table.setCurrentCell(gemma_row, 0)
            dialog.model_table.selectRow(gemma_row)
            self.assertTrue(dialog.delete_button.isEnabled())
            self.assertFalse(dialog.install_button.isEnabled())
            self.assertEqual(dialog.install_button.text(), "분석 모델 선택 제외")
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
            self.assertEqual(dialog.progress.format(), "80%")
            self.assertIn("다운로드 중", dialog.progress_status.text())
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
                dialog.model_table.item(row, 0).data(Qt.UserRole)
                for row in range(dialog.model_table.rowCount())
                if "설치됨" in dialog.model_table.item(row, 2).text()
            }
            self.assertNotIn("gemma3:12b", installed_after_delete)
            self.assertEqual(refresh_flag_during_message, [True])
            self.assertEqual(dialog.progress.format(), "삭제 완료")
            dialog._worker = None
            dialog.close()

    def test_ollama_runtime_setup_copies_download_url_when_missing(self):
        from PyQt5.QtWidgets import QApplication, QMessageBox

        from paper_organizer.application.ai_settings import AiSettingsController
        from paper_organizer.infra.ollama_installer import (
            OLLAMA_DOWNLOAD_URL,
            OllamaRuntimeState,
        )
        from paper_organizer.ui.ollama_model_dialog import OllamaModelDialog

        with tempfile.TemporaryDirectory() as temp:
            controller = AiSettingsController(
                MemorySecretStore(),
                Path(temp) / "settings.json",
            )
            dialog = OllamaModelDialog(controller)
            with (
                mock.patch(
                    "paper_organizer.ui.ollama_model_dialog.inspect_runtime",
                    return_value=OllamaRuntimeState(
                        installed=False,
                        running=False,
                        version="",
                        can_install_with_winget=True,
                    ),
                ),
                mock.patch(
                    "paper_organizer.ui.ollama_model_dialog.QMessageBox.information",
                    return_value=QMessageBox.Ok,
                ) as information,
                mock.patch(
                    "paper_organizer.ui.ollama_model_dialog._RuntimeSetupWorker"
                ) as worker,
            ):
                dialog._setup_runtime()

            information.assert_called_once()
            worker.assert_not_called()
            self.assertEqual(QApplication.clipboard().text(), OLLAMA_DOWNLOAD_URL)
            self.assertEqual(dialog.setup_runtime_button.text(), "Ollama 실행")
            self.assertIn("공식 다운로드", dialog.setup_runtime_button.toolTip())
            self.assertEqual(dialog.open_download_button.text(), "다운로드 주소 복사")
            dialog.close()

    def test_update_dialog_shows_the_versioned_installer_name(self):
        from PyQt5.QtCore import Qt
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
        self.assertFalse(
            bool(dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
        )

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
        from PyQt5.QtCore import QItemSelectionModel, Qt

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
        self.assertFalse(
            bool(dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
        )
        self.assertEqual(dialog.table.columnCount(), 5)
        self.assertEqual(
            [
                dialog.table.horizontalHeaderItem(column).text()
                for column in range(5)
            ],
            ["파일", "판정", "제외 사유", "중복", "추정 제목"],
        )
        self.assertEqual(dialog.table.item(0, 1).text(), "학술 논문")
        self.assertIn("학술 문서 특징", dialog.table.item(0, 2).text())
        self.assertEqual(
            dialog.table.item(0, 3).text(),
            "Published Paper · 같은 문헌 · 0.97",
        )
        self.assertEqual(dialog.table.item(1, 1).text(), "특허")
        self.assertIn("사용자가 새 PDF 검토", dialog.table.item(1, 2).text())
        self.assertEqual(dialog.table.item(1, 3).text(), "없음")
        dialog.table.selectionModel().select(
            dialog.table.model().index(1, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
        self.assertEqual(
            [entry.operation_id for entry in dialog.selected_entries()],
            ["one", "two"],
        )
        dialog.table.setCurrentCell(1, 0)
        self.assertIn("사용자가 새 PDF 검토", dialog.reason_label.text())
        dialog.close()

    def test_splash_uses_a_non_null_fallback_when_asset_is_missing(self):
        from paper_organizer.ui.startup_splash import create_splash

        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing-splash.png"
            with mock.patch(
                "paper_organizer.ui.startup_splash.splash_asset_path",
                return_value=missing,
            ):
                splash = create_splash()

        self.assertFalse(splash.pixmap().isNull())
        splash.close()

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

        def queue_item(key, title, priority, status, last_error=""):
            return AnalysisQueueItem(
                queue_id=f"sha256:{key}",
                path=f"C:/library/{key}.paperpack",
                file_sha256=key,
                title=title,
                status=status,
                priority=priority,
                added_at="2026-07-23T00:00:00+00:00",
                updated_at="2026-07-23T00:00:00+00:00",
                last_error=last_error,
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
            queue_item(
                "b",
                "Beta",
                1,
                "organized_pending_analysis",
                "가용 메모리 부족: 현재 1.2GB",
            ),
            queue_item("c", "Gamma", 0, "failed"),
        ]
        widget = AnalysisQueueWidget(FakeController(items))
        self.assertEqual(widget.table.rowCount(), 2)
        self.assertEqual(
            widget.table.horizontalHeaderItem(3).text(),
            "대기/실패 사유",
        )
        beta_row = next(
            row
            for row in range(widget.table.rowCount())
            if widget.table.item(row, 2).text() == "Beta"
        )
        self.assertIn("가용 메모리 부족", widget.table.item(beta_row, 3).text())
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

    def test_analysis_queue_idle_worker_does_not_emit_busy_progress(self):
        from paper_organizer.ui.library_workflow_widget import AnalysisQueueWidget

        class FakeController:
            def analysis_queue(self):
                return []

        widget = AnalysisQueueWidget(FakeController())
        events = []
        widget.analysis_progress.connect(lambda message, busy: events.append(busy))

        widget._analysis_running = True
        widget._current_analysis_title = ""
        widget._emit_progress()
        self.assertEqual(events[-1], False)

        widget._current_analysis_title = "Queued paper"
        widget._emit_progress()
        self.assertEqual(events[-1], True)
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

    def test_lifecycle_settings_live_in_folder_settings_dialog(self):
        from PyQt5.QtWidgets import QDialog

        from paper_organizer.application.lifecycle import LifecycleSettingsController
        from paper_organizer.application.library_workflow import LibraryWorkflowController
        from paper_organizer.ui.folder_settings_dialog import FolderSettingsDialog

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / "settings.json"
            watch = root / "watch"
            library = root / "library"
            watch.mkdir()
            workflow_controller = LibraryWorkflowController(settings_path)
            workflow_controller.save_paths(
                watch,
                library,
                auto_enabled=True,
                resource_profile="eco",
                scan_interval_seconds=300,
                watch_folders=[watch],
            )
            startup = MemoryLoginStartup()
            lifecycle_controller = LifecycleSettingsController(
                settings_path,
                startup,
            )
            dialog = FolderSettingsDialog(
                workflow_controller,
                lifecycle=lifecycle_controller,
            )

            self.assertIsNotNone(dialog.start_with_windows_check)
            self.assertFalse(dialog.start_with_windows_check.isChecked())
            self.assertTrue(dialog.quit_radio.isChecked())
            dialog.start_with_windows_check.setChecked(True)
            dialog.background_radio.setChecked(True)
            dialog._save()

            self.assertEqual(dialog.result(), QDialog.Accepted)
            self.assertTrue(startup.enabled)
            self.assertTrue(lifecycle_controller.settings().start_with_windows)
            self.assertEqual(
                lifecycle_controller.settings().close_behavior,
                "background",
            )
            dialog.close()

    def test_selecting_same_library_path_renders_analysis_immediately(self):
        from PyQt5.QtCore import QItemSelectionModel
        from PyQt5.QtWidgets import (
            QAbstractItemView,
            QFormLayout,
            QLabel,
            QMenu,
            QMessageBox,
            QWidget,
        )

        from paper_organizer.application.library_workflow import (
            EditablePaperMetadata,
            LibraryEntry,
        )
        from paper_organizer.infra.settings import AppSettings
        from paper_organizer.application.library_translation import (
            LibraryTranslation,
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
                metadata=EditablePaperMetadata(
                    title="Original English Title",
                    tags=["legacy-tag"],
                ),
                work_id="work:test",
                source_variant="publisher",
                paperpack_created_at="2026-07-27T11:30:00",
                analysis_completed_at="2026-07-28T12:00:00",
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
                    self.saved = []
                    self.reanalysis_requests = []
                    self._settings = AppSettings()

                def invalidate_library_cache(self):
                    pass

                def settings(self):
                    return self._settings

                def save_library_column_preferences(self, *, order=None, hidden=None):
                    if order is not None:
                        self._settings.library_column_order = list(order)
                    if hidden is not None:
                        self._settings.library_hidden_columns = list(hidden)
                    return self._settings

                def list_library(self):
                    return [
                        value
                        for value in (entry, second)
                        if value not in self.deleted
                    ]

                def search_library(self, query):
                    self.search_queries.append(query)
                    if query == "thermostable enzyme":
                        return [
                            replace(
                                entry,
                                search_locations=("body",),
                                search_page=2,
                                search_snippet=(
                                    "Methods introduce the assay. "
                                    "Thermostable enzyme activity increased. "
                                    "The next sentence reports controls."
                                ),
                            )
                        ]
                    return self.list_library()

                def analysis_queue(self):
                    return []

                def paperpack_working_copy(self, _path):
                    return None

                def update_library_metadata(self, value, metadata):
                    updated = replace(value, metadata=metadata)
                    self.saved.append((value, metadata))
                    return updated

                def queue_reanalysis(self, entries, *, high=False):
                    self.reanalysis_requests.append((list(entries), high))
                    return len(entries), ()

                def materialize_editable_pdf(self, path):
                    return path

                def trash_library_entries(self, entries):
                    self.deleted.extend(entries)
                    return mock.Mock(deleted=len(entries), problems=())

            class FakeTranslationService:
                def has_source(self, value):
                    return value.sidecar_path == entry.sidecar_path

                def cached(self, value):
                    if value.sidecar_path != entry.sidecar_path:
                        return None
                    return LibraryTranslation(
                        text="[요약]\n한국어 번역 요약",
                        source_hash="source-hash",
                        provider="ollama",
                        model="qwen3:4b",
                        translated_at="2026-07-29T12:00:00",
                    )

            controller = FakeLibraryController()
            translation_service = FakeTranslationService()
            with mock.patch(
                "paper_organizer.ui.library_workflow_widget."
                "analysis_translation_source_hash",
                return_value="source-hash",
            ):
                widget = LibraryWidget(
                    controller,
                    translation_service=translation_service,
                    selection_ai=object(),
                )
            self.assertEqual(widget.open_button.text(), "PDF 열기")
            self.assertEqual(widget.selection_ai_button.text(), "선택 AI")
            self.assertIn("다시 열", widget.selection_ai_button.toolTip())
            self.assertEqual(widget.apply_pdf_button.text(), "적용")
            self.assertEqual(widget.discard_pdf_button.text(), "편집 취소")
            self.assertEqual(widget.delete_button.text(), "휴지통")
            self.assertEqual(widget.permanent_delete_button.text(), "완전 삭제")
            self.assertEqual(widget.reanalyze_selected_button.text(), "선택 재요약")
            self.assertEqual(widget.reanalyze_all_button.text(), "전체 재요약")
            self.assertEqual(widget.paperpack_manage_button.text(), "PaperPack 관리")
            self.assertEqual(
                [
                    action.text()
                    for action in widget.paperpack_manage_button.menu().actions()
                ],
                ["PDF 환원…", "검색 색인 재구축", "구버전 PaperPack 마이그레이션"],
            )
            self.assertEqual(widget.library_title_label.text(), "라이브러리")
            self.assertEqual(widget.library_count_label.text(), "문서 2개")
            self.assertNotIn("라이브러리 문서", widget.status_label.text())
            self.assertEqual(widget.form.title(), "")
            self.assertTrue(widget.type_suggestion_label.isHidden())
            detail_layout = widget.form.parentWidget().layout()
            self.assertLess(
                detail_layout.indexOf(widget.save_button.parentWidget()),
                detail_layout.indexOf(widget.form),
            )
            form_labels = []
            for row in range(widget.form._form_layout.rowCount()):
                item = widget.form._form_layout.itemAt(row, QFormLayout.LabelRole)
                label = item.widget() if item is not None else None
                if isinstance(label, QLabel):
                    form_labels.append(label.text())
            self.assertIn("분야/세부분야", form_labels)
            self.assertNotIn("태그", form_labels)
            self.assertEqual(widget.form.metadata().tags, ["legacy-tag"])
            self.assertFalse(hasattr(widget, "approve_category_button"))
            action_buttons = {
                widget.open_button,
                widget.selection_ai_button,
                widget.apply_pdf_button,
                widget.discard_pdf_button,
                widget.delete_button,
                widget.permanent_delete_button,
                widget.reanalyze_selected_button,
                widget.reanalyze_all_button,
                widget.paperpack_manage_button,
            }
            index_actions = {
                widget.save_button,
            }
            index_action_rows = []
            root_action_rows = []
            for index in range(widget.layout().count()):
                row_layout = widget.layout().itemAt(index).layout()
                if row_layout is None:
                    continue
                row_buttons = {
                    row_layout.itemAt(column).widget()
                    for column in range(row_layout.count())
                    if row_layout.itemAt(column).widget() is not None
                }
                if row_buttons & action_buttons:
                    root_action_rows.append(row_buttons & action_buttons)
                if row_buttons & index_actions:
                    index_action_rows.append(row_buttons & index_actions)
            self.assertEqual(root_action_rows, [])
            self.assertEqual(index_action_rows, [])
            self.assertEqual(
                widget.open_with_ai_button.text(),
                "PDF + AI",
            )
            self.assertFalse(hasattr(widget, "selection_result"))
            self.assertTrue(widget.select_path(paperpack))
            row = widget.table.currentRow()
            with mock.patch(
                "paper_organizer.ui.library_workflow_widget.open_pdf"
            ) as opened:
                widget._open_row(row)
            self.assertNotIn("selection_callback", opened.call_args.kwargs)

            class FakeSaveMessageBox:
                Question = QMessageBox.Question
                AcceptRole = QMessageBox.AcceptRole
                ActionRole = QMessageBox.ActionRole
                Cancel = QMessageBox.Cancel

                next_choice = "save"

                def __init__(self, _parent=None):
                    self._buttons = {}
                    self._clicked = None

                def setWindowTitle(self, _text):
                    pass

                def setIcon(self, _icon):
                    pass

                def setText(self, _text):
                    pass

                def setInformativeText(self, _text):
                    pass

                def addButton(self, label, _role=None):
                    button = object()
                    self._buttons[label] = button
                    return button

                def setDefaultButton(self, _button):
                    pass

                def exec_(self):
                    label = (
                        "저장 후 재요약"
                        if self.next_choice == "reanalyze"
                        else "저장만"
                    )
                    self._clicked = self._buttons[label]

                def clickedButton(self):
                    return self._clicked

            FakeSaveMessageBox.next_choice = "save"
            with mock.patch(
                "paper_organizer.ui.library_workflow_widget.QMessageBox",
                FakeSaveMessageBox,
            ):
                widget._save_selected()
            self.assertEqual(len(controller.saved), 1)
            self.assertEqual(controller.reanalysis_requests, [])

            FakeSaveMessageBox.next_choice = "reanalyze"
            with mock.patch(
                "paper_organizer.ui.library_workflow_widget.QMessageBox",
                FakeSaveMessageBox,
            ):
                widget._save_selected()
            self.assertEqual(len(controller.saved), 2)
            self.assertEqual(len(controller.reanalysis_requests), 1)
            self.assertTrue(controller.reanalysis_requests[0][1])

            opened_window = QWidget()
            with (
                mock.patch(
                    "paper_organizer.ui.library_workflow_widget.open_pdf",
                    return_value=opened_window,
                ) as opened,
                mock.patch.object(widget, "_open_selection_ai_dialog") as ai_dialog,
                mock.patch.object(widget, "_focus_spdf_window") as focus_spdf,
            ):
                widget._open_selected_with_ai()
            self.assertIn("selection_callback", opened.call_args.kwargs)
            ai_dialog.assert_called_once_with(activate=False)
            focus_spdf.assert_called_once_with(opened_window)
            opened_window.close()
            from paper_organizer.integrations.spdf_bridge import SpdfSelection

            spdf_window = QWidget()
            available = self.app.primaryScreen().availableGeometry()
            spdf_window.setGeometry(available)
            spdf_window.show()
            self.app.processEvents()
            original_spdf_geometry = spdf_window.geometry()
            with mock.patch(
                "paper_organizer.ui.library_workflow_widget.active_spdf_window",
                return_value=spdf_window,
            ):
                widget._spdf_selection_changed(
                    SpdfSelection(
                        text="Selected paragraph",
                        pdf_page=2,
                        bounding_boxes=((1.0, 2.0, 3.0, 4.0),),
                        document_id="paper-one",
                        document_path=Path("paper-one.pdf"),
                    )
                )
            self.assertIsNotNone(widget._selection_dialog)
            self.assertIsNone(widget._selection_dialog.parent())
            self.assertTrue(widget._selection_dialog.isVisible())
            self.assertFalse(
                widget._selection_dialog.geometry().intersects(
                    spdf_window.geometry()
                )
            )
            widget._selection_dialog.close()
            self.assertEqual(spdf_window.geometry(), original_spdf_geometry)
            spdf_window.close()
            self.assertEqual(
                widget.table.selectionMode(),
                QAbstractItemView.ExtendedSelection,
            )
            self.assertFalse(widget.search_edit.isClearButtonEnabled())
            self.assertEqual(widget.clear_search_button.text(), "")
            self.assertFalse(widget.clear_search_button.icon().isNull())
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
            self.assertFalse(widget.search_result_bar.isHidden())
            self.assertIn("검색 'thermostable enzyme'", widget.search_result_label.text())
            self.assertIn("본문 1", widget.search_result_label.text())
            self.assertIn("문맥 1개", widget.search_result_label.text())
            self.assertEqual(widget.table.horizontalHeader().visualIndex(8), 8)
            self.assertEqual(widget.table.item(0, 8).text(), "본문 2쪽 · 문맥 있음")
            self.assertIn(
                "Thermostable enzyme activity increased",
                widget.table.item(0, 0).toolTip(),
            )
            captured_menus = []

            def capture_menu(menu, _position):
                captured_menus.append(menu)

            with mock.patch.object(QMenu, "exec_", capture_menu):
                widget._show_context_menu(widget.table.visualItemRect(widget.table.item(0, 0)).center())
            context_actions = [
                action.text()
                for action in captured_menus[0].actions()
                if not action.isSeparator()
            ]
            self.assertIn("선택 영역 창 다시 열기", context_actions)
            self.assertIn("편집본을 PaperPack에 적용", context_actions)
            self.assertIn("PDF 편집 취소", context_actions)
            self.assertIn("AI 추천 연구분야 없음", context_actions)
            self.assertIn("전체 논문 재요약", context_actions)
            self.assertLess(
                context_actions.index("AI 추천 연구분야 없음"),
                context_actions.index("선택 논문 재요약"),
            )
            self.assertTrue(
                all(
                    not action.icon().isNull()
                    for action in captured_menus[0].actions()
                    if not action.isSeparator()
                )
            )
            self.assertIn("분석 요약", widget.analysis_view.toPlainText())
            self.assertEqual(widget.translation_button.text(), "AI 번역 보기")
            self.assertTrue(widget.translation_button.isEnabled())
            widget.translation_button.click()
            self.assertEqual(widget.translation_button.text(), "원문 보기")
            self.assertIn("한국어 번역 요약", widget.analysis_view.toPlainText())
            self.assertNotIn("분석 요약", widget.analysis_view.toPlainText())
            widget.translation_button.click()
            self.assertIn("분석 요약", widget.analysis_view.toPlainText())
            widget.search_edit.clear()
            widget.refresh()
            self.assertEqual(widget.table.columnCount(), 9)
            self.assertEqual(widget.table.horizontalHeaderItem(8).text(), "검색 위치")
            self.assertEqual(
                [
                    widget.table.horizontalHeaderItem(column).text()
                    for column in (5, 6, 7)
                ],
                ["번역 상태", "등록일", "분석일"],
            )
            self.assertEqual(widget.table.item(0, 4).text(), "v1.4.1")
            self.assertEqual(widget.table.item(0, 5).text(), "—")
            self.assertEqual(widget.table.item(0, 6).text(), "2026-07-27")
            self.assertEqual(widget.table.item(0, 7).text(), "2026-07-28")
            header = widget.table.horizontalHeader()
            header.moveSection(header.visualIndex(2), 1)
            self.assertEqual(
                controller.settings().library_column_order[:3],
                ["title", "year", "authors"],
            )
            widget.table.setColumnHidden(5, True)
            widget._save_library_column_preferences()
            self.assertIn(
                "translation_status",
                controller.settings().library_hidden_columns,
            )
            controller.settings().library_column_order = []
            controller.settings().library_hidden_columns = []
            widget._apply_library_column_preferences()
            self.assertEqual(header.visualIndex(8), 8)
            self.assertFalse(widget.table.isColumnHidden(5))
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
                "v9",
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
            self.assertEqual(widget.table.item(0, 4).text(), "실패")
            self.assertIn("AI 요약 실패", failed_text)
            self.assertIn("정규식 추출 Abstract", failed_text)
            self.assertIn("Original abstract fallback.", failed_text)
            self.assertIn("서지정보 입력 형식 검증 실패", failed_text)
            self.assertIn("요청 시도 횟수: 3회", failed_text)
            self.assertIn("ollama / qwen3:4b", failed_text)
            self.assertIn("Abstract, Results", failed_text)
            self.assertNotIn("분석 요약", failed_text)
            entry.record.pop("workflow")
            entry.record["analysis"].pop("last_attempt")
            widget.refresh(True)
            self.assertTrue(widget.select_path(second_pack))
            widget.refresh(True)
            self.assertEqual(widget._selected().sidecar_path, second_pack)
            self.assertTrue(widget.select_path(paperpack))
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
            self.assertTrue(widget.search_result_bar.isHidden())
            self.assertEqual(widget.table.horizontalHeader().visualIndex(8), 8)
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
            with mock.patch(
                "paper_organizer.ui.main_window.QSystemTrayIcon.isSystemTrayAvailable",
                return_value=True,
            ):
                window = PaperOrganizerWindow(
                    AiSettingsController(secret_store, path),
                    LibraryWorkflowController(path),
                    lifecycle=lifecycle,
                )
            window.show()
            self.app.processEvents()
            self.assertIsNotNone(window._tray)
            self.assertTrue(window._tray.isVisible())

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
            self.assertTrue(window._tray.isVisible())
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
