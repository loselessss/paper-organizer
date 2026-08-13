"""Paper Organizer window connecting collection, library and summary workflows."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import QPoint, QSize, Qt, QTimer
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QApplication,
    QFrame,
    QLabel,
    QVBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSystemTrayIcon,
    QToolBar,
    QToolButton,
)

from paper_organizer import __version__
from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.application.background_analysis import BackgroundAnalysisService
from paper_organizer.application.conversational_search import (
    ConversationalSearchController,
)
from paper_organizer.application.lifecycle import LifecycleSettingsController
from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.application.library_translation import (
    LibraryTranslationService,
)
from paper_organizer.application.selection_ai import SelectionAiService
from paper_organizer.application.update_service import (
    AvailableUpdate,
    GitHubUpdateService,
)
from paper_organizer.application.update_schedule import UpdateCheckSchedule

from .ai_settings_dialog import AiSettingsDialog
from .fluent_style import apply_fluent_theme, decorate_action, decorate_button
from .library_workflow_widget import (
    AnalysisQueueWidget,
    CollectionReviewWidget,
    LibraryWidget,
)
from .migration_widget import LegacyMigrationDialog
from .ollama_model_dialog import OllamaModelDialog
from .pdf_export_dialog import PdfExportDialog
from .folder_settings_dialog import FolderSettingsDialog
from .search_chat_dialog import SearchChatDialog
from .startup_splash import app_icon_path
from .update_dialog import UpdateCheckWorker, UpdateDialog


AUTOMATIC_UPDATE_POLL_INTERVAL_MS = 24 * 60 * 60 * 1000


class PaperOrganizerWindow(QMainWindow):
    def __init__(
        self,
        ai_settings: AiSettingsController,
        library_workflow: LibraryWorkflowController | None = None,
        lifecycle: LifecycleSettingsController | None = None,
        background_analysis: BackgroundAnalysisService | None = None,
        conversational_search: ConversationalSearchController | None = None,
        library_translation: LibraryTranslationService | None = None,
        selection_ai: SelectionAiService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        apply_fluent_theme(QApplication.instance())
        self._ai_settings = ai_settings
        self._lifecycle = lifecycle
        self._conversational_search = conversational_search
        self._force_quit = False
        self._tray_message_shown = False
        self._tray: QSystemTrayIcon | None = None
        self._update_service = GitHubUpdateService(__version__)
        self._update_schedule = UpdateCheckSchedule(ai_settings.settings_path)
        self._update_worker: UpdateCheckWorker | None = None
        self._automatic_update_timer = QTimer(self)
        self._automatic_update_timer.setInterval(AUTOMATIC_UPDATE_POLL_INTERVAL_MS)
        self._automatic_update_timer.timeout.connect(
            lambda: self.check_for_updates(False)
        )
        self._available_update: AvailableUpdate | None = None
        self._pending_installer: Path | None = None
        self.setWindowTitle(f"Paper Organizer — v{__version__}")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.resize(1280, 760)

        self._library_workflow = library_workflow
        self._new_pdf_popup: QFrame | None = None
        self._analysis_queue_popup: QFrame | None = None
        self.collection_widget = None
        self.queue_widget = None
        self.library_widget = None
        if library_workflow is not None:
            self.collection_widget = CollectionReviewWidget(library_workflow, self)
            self.queue_widget = AnalysisQueueWidget(
                library_workflow,
                background_analysis,
                self,
            )
            self.library_widget = LibraryWidget(
                library_workflow,
                self,
                translation_service=library_translation,
                selection_ai=selection_ai,
            )
            self.collection_widget.library_changed.connect(self.library_widget.refresh)
            self.collection_widget.queue_changed.connect(self.queue_widget.refresh)
            self.queue_widget.review_items_dropped.connect(
                self.collection_widget.organize_dropped
            )
            self.collection_widget.papers_auto_organized.connect(
                self._papers_auto_organized
            )
            self.collection_widget.immediate_analysis_requested.connect(
                lambda count: self.queue_widget.start_background_analysis(
                    immediate_count=count
                )
            )
            self.collection_widget.library_requested.connect(
                self._open_queue_item_in_library
            )
            self._new_pdf_popup = self._create_workflow_popup(
                "newPdfReviewPopup",
                self.collection_widget,
                minimum_size=QSize(760, 420),
            )
            self._analysis_queue_popup = self._create_workflow_popup(
                "analysisQueuePopup",
                self.queue_widget,
                minimum_size=QSize(620, 300),
            )
        if self.queue_widget is not None:
            self.queue_widget.library_requested.connect(
                self._open_queue_item_in_library
            )
            self.queue_widget.library_changed.connect(
                lambda: self.library_widget.refresh(True)
                if self.library_widget is not None
                else None
            )
            self.library_widget.reanalysis_queued.connect(
                self._library_reanalysis_queued
            )
            self.library_widget.translation_queued.connect(
                self._library_translation_queued
            )
            self.library_widget.metadata_changed.connect(
                self.queue_widget.refresh
            )
            self.library_widget.natural_search_requested.connect(
                self.show_natural_search
            )
            self.library_widget.pdf_export_requested.connect(self.show_pdf_export)
            self.library_widget.search_rebuild_requested.connect(
                self.rebuild_search_index
            )
            self.library_widget.legacy_migration_requested.connect(
                self.show_legacy_migration
            )
            self.library_widget.actions_changed.connect(
                self._sync_library_ribbon_actions
            )
        if self.library_widget is not None:
            self.setCentralWidget(self.library_widget)
        self._engine_settings_menu = QMenu(self)
        self._library_ribbon_action_bindings: list[tuple[QAction, object]] = []
        self._library_ribbon_menu_buttons: list[QToolButton] = []
        self._paperpack_advanced_action: QAction | None = None
        self._legacy_migration_action: QAction | None = None
        self._create_ai_menu(self._engine_settings_menu)
        self.menuBar().hide()
        self._create_shortcuts()
        self._ensure_system_tray()
        self._create_command_ribbon()
        self._analysis_status_label = QLabel("")
        self._analysis_progress_bar = QProgressBar()
        self._analysis_progress_bar.setRange(0, 1)
        self._analysis_progress_bar.setValue(0)
        self._analysis_progress_bar.setFixedWidth(120)
        self._analysis_progress_bar.setFixedHeight(14)
        self._analysis_progress_bar.setTextVisible(False)
        self._analysis_progress_bar.setVisible(True)
        self._analysis_progress_bar.setMaximumWidth(0)
        self.statusBar().addPermanentWidget(self._analysis_status_label)
        self.statusBar().addPermanentWidget(self._analysis_progress_bar)
        self.statusBar().setFixedHeight(
            max(24, self.statusBar().sizeHint().height())
        )
        if self.queue_widget is not None:
            self.queue_widget.analysis_progress.connect(self._analysis_progress_changed)
            self.queue_widget.refresh()
        self.statusBar().showMessage("다운로드 폴더의 새 논문을 검색할 준비가 되었습니다.")
        if self._library_workflow is not None:
            QTimer.singleShot(0, self._check_legacy_migration_candidates)
        if getattr(sys, "frozen", False):
            QTimer.singleShot(5000, lambda: self.check_for_updates(False))
            self._automatic_update_timer.start()

    def _analysis_progress_changed(self, message: str, busy: bool) -> None:
        self._analysis_status_label.setText(message)
        if busy:
            self._analysis_progress_bar.setRange(0, 0)
            self._analysis_progress_bar.setMaximumWidth(120)
        else:
            self._analysis_progress_bar.setRange(0, 1)
            self._analysis_progress_bar.setValue(0)
            self._analysis_progress_bar.setMaximumWidth(0)

    def changeEvent(self, event) -> None:
        if event.type() == event.WindowStateChange and self.isMinimized():
            self._hide_workflow_popups()
        super().changeEvent(event)

    def hideEvent(self, event) -> None:
        self._hide_workflow_popups()
        super().hideEvent(event)

    def _create_workflow_popup(
        self,
        object_name: str,
        widget,
        *,
        minimum_size: QSize,
    ) -> QFrame:
        popup = QFrame(self)
        popup.setObjectName(object_name)
        popup.setMinimumSize(minimum_size)
        popup_layout = QVBoxLayout(popup)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        popup_layout.addWidget(widget)
        popup.hide()
        return popup

    def _papers_auto_organized(self, titles: list) -> None:
        if not titles:
            return
        preview = ", ".join(str(title) for title in titles[:3])
        if len(titles) > 3:
            preview += f" 외 {len(titles) - 3}건"
        message = f"논문 {len(titles)}건을 자동 보관하고 분석 큐에 넣었습니다: {preview}"
        self.statusBar().showMessage(message, 8000)
        if self._tray is not None and self._tray.isVisible():
            self._tray.showMessage(
                "Paper Organizer", message, QSystemTrayIcon.Information, 5000
            )

    def _library_reanalysis_queued(self, count: int) -> None:
        if self.queue_widget is None:
            return
        self.queue_widget.refresh()
        self.queue_widget.start_background_analysis(immediate_count=count)

    def _library_translation_queued(self, count: int) -> None:
        if self.queue_widget is None:
            return
        self.queue_widget.refresh()
        self.queue_widget.start_background_analysis(immediate_count=count)

    def _create_ai_menu(self, menu: QMenu) -> None:
        self._provider_group = QActionGroup(self)
        self._provider_group.setExclusive(True)
        self._provider_actions: dict[str, QAction] = {}
        for choice in self._ai_settings.view().provider_choices:
            action = QAction(choice.label, self, checkable=True)
            action.setData(choice.provider)
            action.triggered.connect(
                lambda _checked, provider=choice.provider: self._switch_provider(
                    provider
                )
            )
            self._provider_group.addAction(action)
            self._provider_actions[choice.provider] = action
            menu.addAction(action)
        menu.addSeparator()
        ai_settings_action = QAction("제공자·모델·언어·제한 시간...", self)
        decorate_action(ai_settings_action, "settings")
        ai_settings_action.triggered.connect(self.show_ai_settings)
        menu.addAction(ai_settings_action)
        models_action = QAction("Ollama 모델 관리...", self)
        decorate_action(models_action, "download")
        models_action.triggered.connect(self.show_ollama_models)
        menu.addAction(models_action)
        menu.aboutToShow.connect(self._sync_provider_actions)
        self._sync_provider_actions()

    def _create_shortcuts(self) -> None:
        if self.collection_widget is not None:
            scan_action = QAction("새 PDF 검색", self)
            decorate_action(scan_action, "search")
            scan_action.setShortcut(QKeySequence.Refresh)
            scan_action.triggered.connect(lambda: self.collection_widget.scan_now(True))
            self.addAction(scan_action)
        if self.library_widget is not None:
            search_action = QAction("라이브러리 검색", self)
            decorate_action(search_action, "search")
            search_action.setShortcut(QKeySequence.Find)
            search_action.triggered.connect(self.library_widget.search_edit.setFocus)
            self.addAction(search_action)

        if self._conversational_search is not None:
            natural_search_action = QAction("자연어로 논문 찾기", self)
            decorate_action(natural_search_action, "search")
            natural_search_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
            natural_search_action.triggered.connect(
                lambda: self.show_natural_search("")
            )
            self.addAction(natural_search_action)

    def _create_command_ribbon(self) -> None:
        ribbon = QToolBar("빠른 명령", self)
        ribbon.setObjectName("commandRibbon")
        ribbon.setMovable(False)
        ribbon.setFloatable(False)
        ribbon.setIconSize(QSize(14, 14))
        ribbon.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self._command_ribbon = ribbon
        self.addToolBar(Qt.TopToolBarArea, ribbon)

        def add_command(
            text: str,
            icon_name: str,
            slot,
            tooltip: str = "",
        ) -> QAction:
            action = QAction(text, self)
            decorate_action(action, icon_name)
            if tooltip:
                action.setToolTip(tooltip)
            action.triggered.connect(slot)
            ribbon.addAction(action)
            return action

        if self.collection_widget is not None:
            add_command(
                "새 PDF",
                "search",
                self.show_new_pdf_review,
            )
        if self._analysis_queue_popup is not None:
            add_command("분석 큐", "menu", self.toggle_analysis_queue)

        if self.library_widget is not None:
            ribbon.addSeparator()
            self._create_library_ribbon_menus(ribbon)

        ribbon.addSeparator()
        if self._library_workflow is not None:
            add_command("감시 설정", "folder", self.show_folder_settings)
        add_command("AI 설정", "settings", self.show_ai_settings)

        ribbon.addSeparator()
        add_command("업데이트", "download", lambda: self.check_for_updates(True))
        add_command("단축키", "select", self._show_shortcuts)
        add_command("정보", "help", self._show_about)
        self._sync_library_ribbon_actions()

    def _create_library_ribbon_menus(self, ribbon: QToolBar) -> None:
        if self.library_widget is None:
            return

        def add_menu_button(text: str, icon_name: str) -> tuple[QToolButton, QMenu]:
            button = QToolButton(self)
            button.setText(text)
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setPopupMode(QToolButton.InstantPopup)
            decorate_button(button, icon_name)
            menu = QMenu(button)
            button.setMenu(menu)
            ribbon.addWidget(button)
            self._library_ribbon_menu_buttons.append(button)
            return button, menu

        def add_button_action(menu: QMenu, button, icon_name: str) -> QAction:
            action = QAction(button.text(), self)
            decorate_action(action, icon_name)
            action.setToolTip(button.toolTip())
            action.triggered.connect(button.click)
            menu.addAction(action)
            self._library_ribbon_action_bindings.append((action, button))
            return action

        _button, spdf_menu = add_menu_button("sPDF", "open")
        add_button_action(spdf_menu, self.library_widget.open_button, "open")
        add_button_action(spdf_menu, self.library_widget.open_with_ai_button, "ai")
        add_button_action(spdf_menu, self.library_widget.selection_ai_button, "ai")

        _button, translation_menu = add_menu_button("AI 번역", "translate")
        add_button_action(
            translation_menu,
            self.library_widget.translation_button,
            "translate",
        )
        add_button_action(
            translation_menu,
            self.library_widget.restore_translation_button,
            "restore",
        )

        _button, paperpack_menu = add_menu_button("PaperPack", "archive")
        add_button_action(paperpack_menu, self.library_widget.apply_pdf_button, "save")
        add_button_action(
            paperpack_menu,
            self.library_widget.discard_pdf_button,
            "cancel",
        )
        paperpack_menu.addSeparator()
        export_action = paperpack_menu.addAction("PDF 환원…")
        decorate_action(export_action, "pdf")
        export_action.triggered.connect(self.show_pdf_export)
        rebuild_action = paperpack_menu.addAction("검색 색인 재구축")
        decorate_action(rebuild_action, "refresh")
        rebuild_action.triggered.connect(self.rebuild_search_index)
        advanced_menu = paperpack_menu.addMenu("고급")
        decorate_action(advanced_menu.menuAction(), "settings")
        migration_action = advanced_menu.addAction("구버전 마이그레이션…")
        decorate_action(migration_action, "archive")
        migration_action.triggered.connect(self.show_legacy_migration)
        migration_action.setVisible(False)
        advanced_menu.menuAction().setVisible(False)
        self._paperpack_advanced_action = advanced_menu.menuAction()
        self._legacy_migration_action = migration_action

        _button, delete_menu = add_menu_button("삭제", "delete")
        add_button_action(delete_menu, self.library_widget.delete_button, "delete")
        add_button_action(
            delete_menu,
            self.library_widget.permanent_delete_button,
            "delete",
        )

        _button, reanalysis_menu = add_menu_button("재요약", "refresh")
        add_button_action(
            reanalysis_menu,
            self.library_widget.reanalyze_selected_button,
            "refresh",
        )
        add_button_action(
            reanalysis_menu,
            self.library_widget.reanalyze_all_button,
            "refresh",
        )

    def _sync_library_ribbon_actions(self) -> None:
        for action, button in getattr(self, "_library_ribbon_action_bindings", []):
            action.setText(button.text())
            action.setEnabled(button.isEnabled())
            action.setToolTip(button.toolTip())
            if action.isCheckable():
                action.setChecked(button.isChecked())
        for button in getattr(self, "_library_ribbon_menu_buttons", []):
            menu = button.menu()
            if menu is None:
                continue
            actions = [action for action in menu.actions() if not action.isSeparator()]
            button.setEnabled(any(action.isEnabled() for action in actions))

    def _check_legacy_migration_candidates(self) -> None:
        if self._library_workflow is None:
            return
        try:
            preview = self._library_workflow.legacy_migration_preview()
        except Exception:
            return
        count = len(preview.candidates)
        if self._legacy_migration_action is not None:
            self._legacy_migration_action.setVisible(bool(count))
            self._legacy_migration_action.setText(
                f"구버전 마이그레이션… ({count})"
                if count
                else "구버전 마이그레이션…"
            )
        if self._paperpack_advanced_action is not None:
            self._paperpack_advanced_action.setVisible(bool(count))
        if count:
            self.statusBar().showMessage(
                f"구버전 PaperPack {count}개 확인됨 · "
                "PaperPack > 고급에서 마이그레이션을 실행할 수 있습니다.",
                10000,
            )

    def show_new_pdf_review(self) -> None:
        if (
            self._new_pdf_popup is not None
            and self._new_pdf_popup.isVisible()
        ):
            self._hide_new_pdf_popup()
            return
        self._hide_analysis_queue_popup()
        self._show_workflow_popup(self._new_pdf_popup)

    def toggle_analysis_queue(self) -> None:
        popup = self._analysis_queue_popup
        if popup is None:
            return
        if popup.isVisible():
            self._hide_analysis_queue_popup()
        else:
            self._hide_new_pdf_popup()
            self._show_workflow_popup(popup)

    def show_analysis_queue(self) -> None:
        self._hide_new_pdf_popup()
        self._show_workflow_popup(self._analysis_queue_popup)
        if self.queue_widget is not None:
            self.queue_widget.refresh()

    def _show_workflow_popup(self, popup: QFrame | None) -> None:
        if popup is None:
            return
        self._position_workflow_popup(popup)
        popup.show()
        popup.raise_()

    def _position_workflow_popup(self, popup: QFrame) -> None:
        toolbar = getattr(self, "_command_ribbon", None)
        if toolbar is not None:
            top = toolbar.geometry().bottom() + 1
        else:
            top = 0
        status_top = self.statusBar().geometry().top()
        if status_top <= top:
            status_top = self.height() - self.statusBar().sizeHint().height()
        available_width = max(320, self.width() - 16)
        available_height = max(240, status_top - top - 8)
        popup_width = min(max(760, int(self.width() * 0.72)), available_width)
        popup.resize(popup_width, available_height)
        popup.move(QPoint(8, top))

    def _hide_new_pdf_popup(self) -> None:
        popup = self._new_pdf_popup
        if popup is not None and popup.isVisible():
            popup.hide()

    def _hide_analysis_queue_popup(self) -> None:
        popup = self._analysis_queue_popup
        if popup is not None and popup.isVisible():
            popup.hide()

    def _hide_workflow_popups(self) -> None:
        self._hide_new_pdf_popup()
        self._hide_analysis_queue_popup()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for popup in (self._new_pdf_popup, self._analysis_queue_popup):
            if popup is not None and popup.isVisible():
                self._position_workflow_popup(popup)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "Paper Organizer 정보",
            "<h3>Paper Organizer</h3>"
            "<p>제작: SANGKYU SHIN, Ph.D.</p>"
            "<p>1.0 기능 확장 기여: leonkim25</p>"
            "<p>학술 PDF를 로컬 우선 방식으로 분류·보관·검색합니다.</p>",
        )

    def _show_shortcuts(self) -> None:
        QMessageBox.information(
            self,
            "단축키",
            "F5: 새 PDF 검색\n"
            "Ctrl+F: 라이브러리 검색\n"
            "Ctrl+Shift+F: 자연어로 논문 찾기\n"
            "Ctrl+A: 표 전체 선택\n"
            "Esc: 대화상자 닫기",
        )

    def _sync_provider_actions(self) -> None:
        current = self._ai_settings.settings().summary_provider
        action = self._provider_actions.get(current)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def _switch_provider(self, provider: str) -> None:
        try:
            view = self._ai_settings.set_provider(provider)
        except Exception as exc:
            QMessageBox.warning(self, "AI 제공자 변경 실패", str(exc))
            self._sync_provider_actions()
            return
        message = f"요약 AI 제공자를 {view.provider_label}(으)로 변경했습니다."
        if view.key_required and not view.key_configured:
            message += " API 키를 '요약 엔진 옵션'에서 등록하세요."
        if view.provider == "ollama" and not view.model:
            message += " Ollama 모델을 먼저 선택하세요."
        self.statusBar().showMessage(message)

    def show_ai_settings(self) -> None:
        AiSettingsDialog(self._ai_settings, self).exec_()
        self._sync_provider_actions()

    def show_first_run_ai_setup(self) -> None:
        QMessageBox.information(
            self,
            "첫 실행 설정",
            "먼저 요약 감시 옵션에서 다운로드 폴더, 스캔 주기와 자동 보관 "
            "방식을 확인합니다.\n\n이어서 요약 엔진 옵션에서 로컬 또는 "
            "클라우드 AI를 설정합니다.",
        )
        if self._library_workflow is not None:
            if FolderSettingsDialog(
                self._library_workflow,
                self,
                lifecycle=self._lifecycle,
            ).exec_() and self._lifecycle is not None:
                self._ensure_system_tray()
        QMessageBox.information(
            self,
            "요약 엔진 설정",
            "Paper Organizer는 기본 요약 AI로 Ollama를 이용합니다.\n\n다음 설정 화면에서 "
            "로컬 Ollama를 선택한 뒤 추천 모델의 설치·검증을 진행하세요. "
            "OpenAI 또는 Anthropic을 대신 사용하려면 API 키와 클라우드 전송 "
            "동의를 설정할 수 있습니다.",
        )
        AiSettingsDialog(self._ai_settings, self).exec_()
        self._sync_provider_actions()
        settings = self._ai_settings.settings()
        if settings.summary_provider == "ollama" and not settings.selected_model.strip():
            dialog = OllamaModelDialog(self._ai_settings, self)
            dialog.refresh()
            dialog.exec_()

    def show_ollama_models(self) -> None:
        OllamaModelDialog(self._ai_settings, self).exec_()

    def show_natural_search(self, question: str = "") -> None:
        if self._conversational_search is None:
            return
        resume_background = bool(
            self.queue_widget is not None
            and self.queue_widget.is_background_running()
        )
        if resume_background and not self.queue_widget.pause_background_analysis():
            QMessageBox.information(
                self,
                "백그라운드 분석 마무리 중",
                "현재 논문 분석이 끝난 뒤 자연어 검색을 다시 열어주세요.",
            )
            return
        dialog = SearchChatDialog(self._conversational_search, self)
        dialog.paper_requested.connect(self._open_natural_search_result)
        if question:
            dialog.set_question(question)
            QTimer.singleShot(0, dialog.start_search)
        dialog.exec_()
        if resume_background and self.queue_widget is not None:
            self.queue_widget.start_background_analysis()

    def _open_natural_search_result(self, path: str) -> None:
        if self.library_widget is None:
            return
        self.library_widget.select_path(path)

    def show_pdf_export(self) -> None:
        if self._library_workflow is None:
            return
        PdfExportDialog(self._library_workflow, self).exec_()

    def show_folder_settings(self) -> None:
        if self._library_workflow is None:
            return
        if FolderSettingsDialog(
            self._library_workflow,
            self,
            lifecycle=self._lifecycle,
        ).exec_():
            if self._lifecycle is not None:
                self._ensure_system_tray()
            if self.collection_widget is not None:
                self.collection_widget._reload_watch_settings()
            if self.queue_widget is not None:
                self.queue_widget.refresh()
            if self.library_widget is not None:
                self.library_widget.refresh(True)

    def show_legacy_migration(self) -> None:
        if self._library_workflow is None:
            return
        dialog = LegacyMigrationDialog(self._library_workflow, self)
        if self.library_widget is not None:
            dialog.library_changed.connect(self.library_widget.refresh)
        dialog.exec_()
        self._check_legacy_migration_candidates()

    def rebuild_search_index(self) -> None:
        """본문이 비어 있던 paperpack을 채운 뒤 전문 검색 색인을 다시 만든다."""
        if self._library_workflow is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            filled, backfill_problems = self._library_workflow.backfill_content()
            indexed, index_problems = self._library_workflow.rebuild_search_index()
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "검색 색인 재구축 실패", str(exc))
            return
        QApplication.restoreOverrideCursor()
        problems = (*backfill_problems, *index_problems)
        message = f"논문 {indexed}건을 검색 색인에 넣었습니다."
        if filled:
            message += f" 본문이 없던 {filled}건은 PDF에서 다시 추출했습니다."
        if problems:
            message += f"\n\n확인 필요 {len(problems)}건:\n" + "\n".join(problems[:5])
        QMessageBox.information(self, "검색 색인 재구축", message)
        if self.library_widget is not None:
            self.library_widget.refresh(True)

    def _ensure_system_tray(self) -> None:
        """Keep the tray icon visible for the entire application lifetime."""

        if QSystemTrayIcon.isSystemTrayAvailable():
            if self._tray is None:
                self._create_system_tray()
            self._show_tray()

    def _create_system_tray(self) -> None:
        self._dispose_tray()
        icon = QIcon(str(app_icon_path()))
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("Paper Organizer")
        menu = QMenu(self)
        open_action = menu.addAction("Paper Organizer 열기")
        open_action.triggered.connect(self._show_from_tray)
        menu.addSeparator()
        quit_action = menu.addAction("완전 종료")
        quit_action.triggered.connect(self._quit_from_tray)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        self._tray = tray
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._dispose_tray)

    def _show_tray(self) -> None:
        if self._tray is not None and not self._tray.isVisible():
            self._tray.show()

    def _dispose_tray(self) -> None:
        """Remove the native icon before window/process teardown to avoid ghosts."""

        tray = self._tray
        self._tray = None
        if tray is None:
            return
        tray.hide()
        tray.setContextMenu(None)
        tray.deleteLater()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self._available_update is not None:
            QTimer.singleShot(0, self._show_available_update)

    def show_from_external_request(self) -> None:
        """Raise the existing window when another app launch is attempted."""

        self._show_from_tray()

    def check_for_updates(self, manual: bool = True) -> None:
        if self._pending_installer is not None and self._pending_installer.is_file():
            self._prompt_install_when_idle()
            return
        if not manual and not self._update_schedule.is_due():
            return
        if self._update_worker is not None and self._update_worker.isRunning():
            if manual:
                self.statusBar().showMessage("업데이트를 확인하고 있습니다…", 4000)
            return
        if manual:
            self.statusBar().showMessage("GitHub에서 최신 버전을 확인하는 중입니다…")
        worker = UpdateCheckWorker(self._update_service, self)
        worker.completed.connect(
            lambda update: self._update_check_completed(update, manual)
        )
        worker.failed.connect(
            lambda message: self._update_check_failed(message, manual)
        )
        worker.finished.connect(self._update_check_finished)
        self._update_worker = worker
        worker.start()

    def _update_check_completed(
        self, update: AvailableUpdate | None, manual: bool
    ) -> None:
        try:
            self._update_schedule.mark_checked()
        except Exception:
            pass
        if update is None:
            if manual:
                QMessageBox.information(
                    self,
                    "업데이트 확인",
                    f"현재 v{__version__}이 최신 버전입니다.",
                )
            return
        if not manual and self._update_schedule.is_skipped(update.version):
            return
        self._available_update = update
        message = f"Paper Organizer {update.version} 업데이트가 있습니다."
        self.statusBar().showMessage(message, 10000)
        if self.isVisible() or manual:
            self._show_available_update()
        elif self._tray is not None and self._tray.isVisible():
            self._tray.showMessage(
                "Paper Organizer 업데이트",
                message + " 앱을 열어 설치할 수 있습니다.",
                QSystemTrayIcon.Information,
                8000,
            )

    def _update_check_failed(self, message: str, manual: bool) -> None:
        if manual:
            QMessageBox.warning(self, "업데이트 확인 실패", message)

    def _update_check_finished(self) -> None:
        worker = self._update_worker
        self._update_worker = None
        if worker is not None:
            worker.deleteLater()

    def _show_available_update(self) -> None:
        update = self._available_update
        if update is None:
            return
        self._available_update = None
        dialog = UpdateDialog(self._update_service, update, self)
        dialog.install_requested.connect(self._update_downloaded)
        dialog.skip_requested.connect(self._skip_update_version)
        dialog.exec_()

    def _skip_update_version(self, version: str) -> None:
        try:
            self._update_schedule.skip_version(version)
        except Exception as exc:
            QMessageBox.warning(self, "업데이트 설정 저장 실패", str(exc))
            return
        self.statusBar().showMessage(
            f"v{version} 자동 알림을 건너뜁니다. 수동 업데이트 확인은 계속 가능합니다.",
            7000,
        )

    def _update_downloaded(self, path: Path) -> None:
        self._pending_installer = path
        self._prompt_install_when_idle()

    def _prompt_install_when_idle(self) -> None:
        path = self._pending_installer
        if path is None or not path.is_file():
            self._pending_installer = None
            return
        busy = bool(
            (self.collection_widget and self.collection_widget.is_busy())
            or (self.queue_widget and self.queue_widget.is_analysis_busy())
            or (self.library_widget and self.library_widget.is_translation_busy())
        )
        if busy:
            self.statusBar().showMessage(
                "업데이트 준비 완료 · 현재 작업이 끝나면 설치 여부를 다시 묻습니다."
            )
            QTimer.singleShot(5000, self._prompt_install_when_idle)
            return
        if QMessageBox.question(
            self,
            "업데이트 설치",
            "업데이트 설치파일 준비가 끝났습니다.\n"
            "Paper Organizer를 종료하고 설치를 시작할까요?",
        ) != QMessageBox.Yes:
            self.statusBar().showMessage(
                "업데이트가 준비되어 있습니다. 도움말 → 업데이트 확인에서 설치할 수 있습니다."
            )
            return
        try:
            self._update_service.launch_installer(path)
        except Exception as exc:
            QMessageBox.warning(self, "업데이트 실행 실패", str(exc))
            return
        self._pending_installer = None
        self._force_quit = True
        if self.queue_widget is not None:
            self.queue_widget.shutdown_background_analysis()
        if self._tray is not None:
            self._dispose_tray()
        QApplication.instance().quit()

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        self._show_from_tray()
        if not self.close():
            self._force_quit = False
            return
        if self._tray is not None:
            self._dispose_tray()
        QApplication.instance().quit()

    def start_in_background(self) -> bool:
        if (
            self._lifecycle is None
            or self._lifecycle.settings().close_behavior != "background"
            or self._tray is None
            or not QSystemTrayIcon.isSystemTrayAvailable()
        ):
            return False
        self._show_tray()
        self.hide()
        return True

    def _open_queue_item_in_library(self, path: str) -> None:
        if self.library_widget is None:
            return
        if not self.library_widget.select_path(path):
            QMessageBox.information(
                self,
                "라이브러리 항목 없음",
                "완료된 분석 항목에 해당하는 라이브러리 파일을 찾지 못했습니다.",
            )

    def closeEvent(self, event) -> None:
        self._hide_workflow_popups()
        if self.collection_widget and self.collection_widget.is_busy():
            QMessageBox.information(
                self,
                "검색 진행 중",
                "현재 PDF 검색이 끝난 뒤 프로그램을 닫으세요.",
            )
            event.ignore()
            return
        if (
            not self._force_quit
            and self._lifecycle is not None
            and self._lifecycle.settings().close_behavior == "background"
        ):
            if self._tray is not None and QSystemTrayIcon.isSystemTrayAvailable():
                event.ignore()
                self.hide()
                self._show_tray()
                if not self._tray_message_shown:
                    self._tray.showMessage(
                        "Paper Organizer",
                        "백그라운드에서 계속 실행 중입니다. 트레이 아이콘으로 다시 열 수 있습니다.",
                        QSystemTrayIcon.Information,
                        4000,
                    )
                    self._tray_message_shown = True
                return
            QMessageBox.warning(
                self,
                "시스템 트레이를 사용할 수 없음",
                "현재 환경에서는 백그라운드로 숨길 수 없어 프로그램을 종료합니다.",
            )
        if self.queue_widget is not None and self.queue_widget.is_analysis_busy():
            QMessageBox.information(
                self,
                "백그라운드 분석 진행 중",
                "현재 논문 분석이 안전하게 끝난 뒤 프로그램을 종료하세요. "
                "창을 닫아 트레이로 보내는 것은 가능합니다.",
            )
            event.ignore()
            return
        if (
            self.library_widget is not None
            and self.library_widget.is_translation_busy()
        ):
            QMessageBox.information(
                self,
                "AI 번역 진행 중",
                "현재 번역이 끝난 뒤 프로그램을 종료하세요. "
                "창을 닫아 트레이로 보내는 것은 가능합니다.",
            )
            event.ignore()
            return
        if self.queue_widget is not None:
            self.queue_widget.shutdown_background_analysis()
        if self._update_worker is not None and self._update_worker.isRunning():
            self._update_worker.requestInterruption()
            self._update_worker.wait(1000)
        if self.library_widget is not None:
            self.library_widget.close_selection_ai_dialog()
        if self._tray is not None:
            self._dispose_tray()
        super().closeEvent(event)
