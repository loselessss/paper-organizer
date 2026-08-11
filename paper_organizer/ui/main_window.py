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
    QTabWidget,
    QToolBar,
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
from .fluent_style import apply_fluent_theme, decorate_action
from .library_workflow_widget import (
    AnalysisQueueWidget,
    CollectionReviewWidget,
    LibraryWidget,
)
from .lifecycle_dialog import LifecyclePreferencesDialog
from .migration_widget import LegacyMigrationDialog
from .ollama_model_dialog import OllamaModelDialog
from .pdf_export_dialog import PdfExportDialog
from .folder_settings_dialog import FolderSettingsDialog
from .search_chat_dialog import SearchChatDialog
from .startup_splash import app_icon_path
from .update_dialog import UpdateCheckWorker, UpdateDialog


AUTOMATIC_UPDATE_POLL_INTERVAL_MS = 60 * 60 * 1000


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
            self.analysis_tabs = QTabWidget()
            self.analysis_tabs.setObjectName("analysisQueueTabs")
            self.analysis_tabs.addTab(self.collection_widget, "새 PDF")
            self.analysis_tabs.addTab(self.queue_widget, "분석 큐")
            popup = QFrame(self, Qt.Tool | Qt.FramelessWindowHint)
            popup.setObjectName("analysisQueuePopup")
            popup.setMinimumSize(760, 520)
            popup_layout = QVBoxLayout(popup)
            popup_layout.setContentsMargins(0, 0, 0, 0)
            popup_layout.addWidget(self.analysis_tabs)
            self._analysis_queue_popup = popup
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
        if self.library_widget is not None:
            self.setCentralWidget(self.library_widget)
        self._engine_settings_menu = QMenu(self)
        self._create_ai_menu(self._engine_settings_menu)
        self.menuBar().hide()
        self._create_shortcuts()
        self._ensure_system_tray()
        self._create_command_ribbon()
        self._analysis_status_label = QLabel("")
        self._analysis_progress_bar = QProgressBar()
        self._analysis_progress_bar.setRange(0, 0)
        self._analysis_progress_bar.setFixedWidth(120)
        self._analysis_progress_bar.hide()
        self.statusBar().addPermanentWidget(self._analysis_status_label)
        self.statusBar().addPermanentWidget(self._analysis_progress_bar)
        if self.queue_widget is not None:
            self.queue_widget.analysis_progress.connect(self._analysis_progress_changed)
            self.queue_widget.refresh()
        self.statusBar().showMessage("다운로드 폴더의 새 논문을 검색할 준비가 되었습니다.")
        if getattr(sys, "frozen", False):
            QTimer.singleShot(5000, lambda: self.check_for_updates(False))
            self._automatic_update_timer.start()

    def _analysis_progress_changed(self, message: str, busy: bool) -> None:
        self._analysis_status_label.setText(message)
        self._analysis_progress_bar.setVisible(busy)

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

        if self._analysis_queue_popup is not None:
            add_command("분석 큐", "menu", self.toggle_analysis_queue)
            ribbon.addSeparator()
        if self.collection_widget is not None:
            add_command(
                "새 PDF",
                "search",
                self.show_new_pdf_review,
            )
        if self.library_widget is not None:
            add_command("검색", "search", self.library_widget.search_edit.setFocus)

        ribbon.addSeparator()
        if self._library_workflow is not None:
            add_command("감시 설정", "folder", self.show_folder_settings)
        add_command("AI 설정", "settings", self.show_ai_settings)
        add_command("모델", "download", self.show_ollama_models)
        if self._lifecycle is not None:
            add_command("시작/종료", "settings", self.show_lifecycle_settings)

        if self._library_workflow is not None:
            ribbon.addSeparator()
            if self._conversational_search is not None:
                add_command(
                    "자연어 검색",
                    "search",
                    lambda: self.show_natural_search(""),
                )
            add_command("PDF 환원", "pdf", self.show_pdf_export)
            add_command("재구축", "refresh", self.rebuild_search_index)
            add_command("마이그레이션", "archive", self.show_legacy_migration)

        ribbon.addSeparator()
        add_command("업데이트", "download", lambda: self.check_for_updates(True))
        add_command("단축키", "select", self._show_shortcuts)
        add_command("정보", "help", self._show_about)

    def show_new_pdf_review(self) -> None:
        self._show_analysis_queue_popup("review")
        if self.collection_widget is not None:
            self.collection_widget.scan_now(True)

    def toggle_analysis_queue(self) -> None:
        popup = self._analysis_queue_popup
        if popup is None:
            return
        if popup.isVisible():
            popup.hide()
        else:
            self._show_analysis_queue_popup("queue")

    def show_analysis_queue(self) -> None:
        self._show_analysis_queue_popup("queue")
        if self.queue_widget is not None:
            self.queue_widget.refresh()

    def _show_analysis_queue_popup(self, page: str = "queue") -> None:
        popup = self._analysis_queue_popup
        if popup is None:
            return
        tabs = getattr(self, "analysis_tabs", None)
        if tabs is not None:
            tabs.setCurrentIndex(0 if page == "review" else 1)
        toolbar = getattr(self, "_command_ribbon", None)
        if toolbar is not None:
            origin = toolbar.mapToGlobal(QPoint(8, toolbar.height()))
        else:
            origin = self.mapToGlobal(QPoint(8, 0))
        toolbar_height = toolbar.height() if toolbar is not None else 0
        available_height = max(520, self.height() - toolbar_height - 72)
        popup_width = min(max(760, int(self.width() * 0.72)), self.width() - 32)
        popup.resize(popup_width, available_height)
        popup.move(origin)
        popup.show()
        popup.raise_()

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
            FolderSettingsDialog(self._library_workflow, self).exec_()
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
        if FolderSettingsDialog(self._library_workflow, self).exec_():
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

    def show_lifecycle_settings(self) -> None:
        if self._lifecycle is None:
            return
        dialog = LifecyclePreferencesDialog(
            self._lifecycle, first_run=False, parent=self
        )
        if not dialog.exec_():
            return
        self._ensure_system_tray()

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
