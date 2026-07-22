"""Initial Paper Organizer shell connecting AI settings and immediate summary."""

from __future__ import annotations

from PyQt5.QtWidgets import QAction, QMainWindow, QMessageBox, QTabWidget

from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.application.summary_service import ImmediateSummaryController

from .ai_settings_dialog import AiSettingsDialog
from .immediate_summary_widget import ImmediateSummaryWidget


class PaperOrganizerWindow(QMainWindow):
    def __init__(
        self,
        ai_settings: AiSettingsController,
        immediate_summary: ImmediateSummaryController,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._ai_settings = ai_settings
        self.setWindowTitle("Paper Organizer")
        self.resize(980, 720)

        self.tabs = QTabWidget()
        self.summary_widget = ImmediateSummaryWidget(immediate_summary, self)
        self.tabs.addTab(self.summary_widget, "즉시 요약")
        self.setCentralWidget(self.tabs)

        settings_action = QAction("요약 AI 설정...", self)
        settings_action.triggered.connect(self.show_ai_settings)
        settings_menu = self.menuBar().addMenu("설정")
        settings_menu.addAction(settings_action)
        self.statusBar().showMessage(
            "즉시 요약은 임시 분석이며 파일을 이동하거나 정식 색인하지 않습니다."
        )

    def show_ai_settings(self) -> None:
        AiSettingsDialog(self._ai_settings, self).exec_()

    def closeEvent(self, event) -> None:
        if self.summary_widget.is_busy():
            QMessageBox.information(
                self,
                "요약 진행 중",
                "현재 요청이 끝난 뒤 프로그램을 닫으세요.",
            )
            event.ignore()
            return
        super().closeEvent(event)
