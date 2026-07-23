"""Paper Organizer window connecting collection, library and summary workflows."""

from __future__ import annotations

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QTabWidget,
)

from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.application.background_analysis import BackgroundAnalysisService
from paper_organizer.application.lifecycle import LifecycleSettingsController
from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.application.summary_service import ImmediateSummaryController

from .ai_settings_dialog import AiSettingsDialog
from .immediate_summary_widget import ImmediateSummaryWidget
from .library_workflow_widget import (
    AnalysisQueueWidget,
    CollectionReviewWidget,
    LibraryWidget,
)
from .lifecycle_dialog import LifecyclePreferencesDialog
from .migration_widget import LegacyMigrationWidget
from .startup_splash import splash_asset_path


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
        self._lifecycle = lifecycle
        self._force_quit = False
        self._tray_message_shown = False
        self._tray: QSystemTrayIcon | None = None
        self.setWindowTitle("Paper Organizer")
        self.resize(980, 720)

        self.tabs = QTabWidget()
        self.collection_widget = None
        self.queue_widget = None
        self.library_widget = None
        self.migration_widget = None
        if library_workflow is not None:
            self.collection_widget = CollectionReviewWidget(library_workflow, self)
            self.queue_widget = AnalysisQueueWidget(
                library_workflow,
                background_analysis,
                self,
            )
            self.library_widget = LibraryWidget(library_workflow, self)
            self.migration_widget = LegacyMigrationWidget(library_workflow, self)
            self.collection_widget.library_changed.connect(self.library_widget.refresh)
            self.collection_widget.queue_changed.connect(self.queue_widget.refresh)
            self.migration_widget.library_changed.connect(self.library_widget.refresh)
            self.tabs.addTab(self.collection_widget, "수집 및 검토")
            self.tabs.addTab(self.queue_widget, "분석 큐")
            self.tabs.addTab(self.library_widget, "라이브러리")
            self.tabs.addTab(self.migration_widget, "레거시 변환")
        self.summary_widget = ImmediateSummaryWidget(immediate_summary, self)
        self.tabs.addTab(self.summary_widget, "즉시 요약")
        if self.queue_widget is not None:
            self.queue_widget.summary_requested.connect(self._open_queue_in_summary)
        self.setCentralWidget(self.tabs)

        settings_action = QAction("요약 AI 설정...", self)
        settings_action.triggered.connect(self.show_ai_settings)
        settings_menu = self.menuBar().addMenu("설정")
        settings_menu.addAction(settings_action)
        if self._lifecycle is not None:
            lifecycle_action = QAction("시작 및 종료 설정...", self)
            lifecycle_action.triggered.connect(self.show_lifecycle_settings)
            settings_menu.addAction(lifecycle_action)
            self._create_system_tray()
        self.statusBar().showMessage("다운로드 폴더의 새 논문을 검색할 준비가 되었습니다.")

    def show_ai_settings(self) -> None:
        AiSettingsDialog(self._ai_settings, self).exec_()

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
        icon = QIcon(str(splash_asset_path()))
        self.setWindowIcon(icon)
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
        self.summary_widget.select_pdf(path)
        self.tabs.setCurrentWidget(self.summary_widget)
        self.statusBar().showMessage(
            "분석 큐의 PDF를 빠른 요약에 넣었습니다. 미리보기 전에는 전송되지 않습니다."
        )

    def closeEvent(self, event) -> None:
        collection_busy = bool(
            self.collection_widget and self.collection_widget.is_busy()
        )
        migration_busy = bool(
            self.migration_widget and self.migration_widget.is_busy()
        )
        if self.summary_widget.is_busy() or collection_busy or migration_busy:
            QMessageBox.information(
                self,
                "요약 진행 중",
                "현재 요청이 끝난 뒤 프로그램을 닫으세요.",
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
