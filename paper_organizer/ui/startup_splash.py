"""Branded splash screen and asynchronous JSON startup loader."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import QLabel, QSplashScreen, QWidget

from paper_organizer import __version__
from paper_organizer.application.library_workflow import LibraryWorkflowController


CREATOR = "SANGKYU SHIN, Ph.D."


class StartupLoader(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller

    def run(self) -> None:
        try:
            self.completed.emit(self._controller.warm_startup_cache())
        except Exception as exc:
            self.failed.emit(str(exc))


def splash_asset_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "paper-organizer-splash.png"


def create_splash() -> QSplashScreen:
    source = QPixmap(str(splash_asset_path()))
    if source.isNull():
        source = QPixmap(760, 430)
        source.fill(QColor("#0b1e3a"))
    canvas = source.scaled(760, 430, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    splash = QSplashScreen(canvas, Qt.WindowStaysOnTopHint)
    panel = QWidget(splash)
    panel.setGeometry(0, 0, 760, 148)
    panel.setStyleSheet("background-color: rgba(4, 16, 37, 178);")
    title = QLabel("Paper Organizer", panel)
    title.setGeometry(34, 18, 520, 48)
    title.setStyleSheet(
        "color: white; background: transparent; font-family: 'Segoe UI'; "
        "font-size: 25pt; font-weight: 700;"
    )
    version = QLabel(f"Version {__version__}", panel)
    version.setGeometry(36, 72, 300, 25)
    version.setStyleSheet(
        "color: #bcefff; background: transparent; font-family: 'Segoe UI'; font-size: 11pt;"
    )
    creator = QLabel(f"Created by {CREATOR}", panel)
    creator.setGeometry(36, 101, 420, 25)
    creator.setStyleSheet(
        "color: #d8e4f3; background: transparent; font-family: 'Segoe UI'; font-size: 11pt;"
    )
    splash.showMessage(
        "JSON 색인과 메타데이터를 읽는 중…",
        Qt.AlignLeft | Qt.AlignBottom,
        QColor("#ffffff"),
    )
    return splash
