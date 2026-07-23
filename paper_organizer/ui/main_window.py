"""Paper Organizer window connecting collection, library and summary workflows."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QApplication,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
)

from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.application.background_analysis import BackgroundAnalysisService
from paper_organizer.application.lifecycle import LifecycleSettingsController
from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.application.summary_service import ImmediateSummaryController

from .ai_settings_dialog import AiSettingsDialog
from .immediate_summary_widget import ImmediateSummaryDialog
from .library_workflow_widget import (
    AnalysisQueueWidget,
    CollectionReviewWidget,
    LibraryWidget,
)
from .lifecycle_dialog import LifecyclePreferencesDialog
from .migration_widget import LegacyMigrationDialog
from .ollama_model_dialog import OllamaModelDialog
from .pdf_export_dialog import PdfExportDialog
from .startup_splash import app_icon_path


class PaperOrganizerWindow(QMainWindow):
    def __init__(
        self,
        ai_settings: AiSettingsController,
        immediate_summary: ImmediateSummaryController,
        library_workflow: LibraryWorkflowController | None = None,
        lifecycle: LifecycleSettingsController | None = None,
        background_analysis: BackgroundAnalysisService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._ai_settings = ai_settings
        self._immediate_summary = immediate_summary
        self._lifecycle = lifecycle
        self._force_quit = False
        self._tray_message_shown = False
        self._tray: QSystemTrayIcon | None = None
        self.setWindowTitle("Paper Organizer")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.resize(1280, 760)

        self._library_workflow = library_workflow
        self.tabs = QTabWidget()
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
            self.library_widget = LibraryWidget(library_workflow, self)
            self.collection_widget.library_changed.connect(self.library_widget.refresh)
            self.collection_widget.queue_changed.connect(self.queue_widget.refresh)
            self.collection_widget.papers_auto_organized.connect(
                self._papers_auto_organized
            )
            collect_split = QSplitter(Qt.Horizontal)
            collect_split.addWidget(self.collection_widget)
            collect_split.addWidget(self.queue_widget)
            collect_split.setStretchFactor(0, 3)
            collect_split.setStretchFactor(1, 2)
            collect_split.setChildrenCollapsible(False)
            self.tabs.addTab(collect_split, "수집 및 분석")
            self.tabs.addTab(self.library_widget, "라이브러리")
        if self.queue_widget is not None:
            self.queue_widget.summary_requested.connect(self._open_queue_in_summary)
        self.setCentralWidget(self.tabs)

        settings_menu = self.menuBar().addMenu("설정")
        if self._library_workflow is not None:
            tools_menu = self.menuBar().addMenu("도구")
            export_action = QAction("PDF 환원 (일괄 추출)...", self)
            export_action.triggered.connect(self.show_pdf_export)
            tools_menu.addAction(export_action)
            migration_action = QAction("레거시 라이브러리 변환...", self)
            migration_action.triggered.connect(self.show_legacy_migration)
            tools_menu.addAction(migration_action)
        self._create_ai_menu()
        if self._lifecycle is not None:
            lifecycle_action = QAction("시작 및 종료 설정...", self)
            lifecycle_action.triggered.connect(self.show_lifecycle_settings)
            settings_menu.addAction(lifecycle_action)
            self._create_system_tray()
        if not settings_menu.actions():
            settings_menu.menuAction().setVisible(False)
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

    def _create_ai_menu(self) -> None:
        menu = self.menuBar().addMenu("AI")
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
        summary_action = QAction("즉시 요약...", self)
        summary_action.triggered.connect(self.show_immediate_summary)
        menu.addAction(summary_action)
        menu.addSeparator()
        ai_settings_action = QAction("요약 AI 설정...", self)
        ai_settings_action.triggered.connect(self.show_ai_settings)
        menu.addAction(ai_settings_action)
        models_action = QAction("Ollama 모델 관리...", self)
        models_action.triggered.connect(self.show_ollama_models)
        menu.addAction(models_action)
        menu.aboutToShow.connect(self._sync_provider_actions)
        self._sync_provider_actions()

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
            message += " API 키를 '요약 AI 설정'에서 등록하세요."
        if view.provider == "ollama" and not view.model:
            message += " Ollama 모델을 먼저 선택하세요."
        self.statusBar().showMessage(message)

    def show_ai_settings(self) -> None:
        AiSettingsDialog(self._ai_settings, self).exec_()
        self._sync_provider_actions()

    def show_ollama_models(self) -> None:
        OllamaModelDialog(self._ai_settings, self).exec_()

    def show_immediate_summary(self, path: str = "") -> None:
        dialog = ImmediateSummaryDialog(self._immediate_summary, self)
        if path:
            dialog.select_pdf(path)
        dialog.exec_()

    def show_pdf_export(self) -> None:
        if self._library_workflow is None:
            return
        PdfExportDialog(self._library_workflow, self).exec_()

    def show_legacy_migration(self) -> None:
        if self._library_workflow is None:
            return
        dialog = LegacyMigrationDialog(self._library_workflow, self)
        if self.library_widget is not None:
            dialog.library_changed.connect(self.library_widget.refresh)
        dialog.exec_()

    def show_lifecycle_settings(self) -> None:
        if self._lifecycle is None:
            return
        dialog = LifecyclePreferencesDialog(
            self._lifecycle, first_run=False, parent=self
        )
        if dialog.exec_() and self._tray is not None:
            if self._lifecycle.settings().close_behavior == "quit":
                self._tray.hide()
            elif QSystemTrayIcon.isSystemTrayAvailable():
                self._tray.show()

    def _create_system_tray(self) -> None:
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

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        self._force_quit = True
        self._show_from_tray()
        if not self.close():
            self._force_quit = False
            return
        if self._tray is not None:
            self._tray.hide()
        QApplication.instance().quit()

    def start_in_background(self) -> bool:
        if (
            self._lifecycle is None
            or self._lifecycle.settings().close_behavior != "background"
            or self._tray is None
            or not QSystemTrayIcon.isSystemTrayAvailable()
        ):
            return False
        self._tray.show()
        self.hide()
        return True

    def _open_queue_in_summary(self, path: str) -> None:
        self.statusBar().showMessage(
            "분석 큐의 PDF를 빠른 요약에 넣었습니다. 미리보기 전에는 전송되지 않습니다."
        )
        self.show_immediate_summary(path)

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
                self._tray.show()
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
        if self.queue_widget is not None:
            self.queue_widget.shutdown_background_analysis()
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)
