"""Paper Organizer window connecting collection, library and summary workflows."""

from __future__ import annotations

from PyQt5.QtWidgets import QAction, QMainWindow, QMessageBox, QTabWidget

from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.application.summary_service import ImmediateSummaryController

from .ai_settings_dialog import AiSettingsDialog
from .immediate_summary_widget import ImmediateSummaryWidget
from .library_workflow_widget import CollectionReviewWidget, LibraryWidget


class PaperOrganizerWindow(QMainWindow):
    def __init__(
        self,
        ai_settings: AiSettingsController,
        immediate_summary: ImmediateSummaryController,
        library_workflow: LibraryWorkflowController | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._ai_settings = ai_settings
        self.setWindowTitle("Paper Organizer")
        self.resize(980, 720)

        self.tabs = QTabWidget()
        self.collection_widget = None
        self.library_widget = None
        if library_workflow is not None:
            self.collection_widget = CollectionReviewWidget(library_workflow, self)
            self.library_widget = LibraryWidget(library_workflow, self)
            self.collection_widget.library_changed.connect(self.library_widget.refresh)
            self.tabs.addTab(self.collection_widget, "수집 및 검토")
            self.tabs.addTab(self.library_widget, "라이브러리")
        self.summary_widget = ImmediateSummaryWidget(immediate_summary, self)
        self.tabs.addTab(self.summary_widget, "즉시 요약")
        self.setCentralWidget(self.tabs)

        settings_action = QAction("요약 AI 설정...", self)
        settings_action.triggered.connect(self.show_ai_settings)
        settings_menu = self.menuBar().addMenu("설정")
        settings_menu.addAction(settings_action)
        self.statusBar().showMessage("다운로드 폴더의 새 논문을 검색할 준비가 되었습니다.")

    def show_ai_settings(self) -> None:
        AiSettingsDialog(self._ai_settings, self).exec_()

    def closeEvent(self, event) -> None:
        collection_busy = bool(
            self.collection_widget and self.collection_widget.is_busy()
        )
        if self.summary_widget.is_busy() or collection_busy:
            QMessageBox.information(
                self,
                "요약 진행 중",
                "현재 요청이 끝난 뒤 프로그램을 닫으세요.",
            )
            event.ignore()
            return
        super().closeEvent(event)
