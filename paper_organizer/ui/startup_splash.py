"""Branded splash screen and asynchronous library startup loader."""

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


def app_icon_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "paper-organizer.ico"


def create_splash() -> QSplashScreen:
    canvas = QPixmap(760, 430)
    canvas.fill(QColor("#0b1e3a"))
    source = QPixmap(str(splash_asset_path()))
    if not source.isNull():
        scaled = source.scaled(
            760,
            430,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        if not scaled.isNull():
            canvas = scaled
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
    creator = QLabel(f"Created by {CREATOR}", splash)
    creator.setObjectName("splashCreatorLabel")
    creator.setGeometry(430, 398, 300, 22)
    creator.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    creator.setStyleSheet(
        "color: rgba(216, 228, 243, 190); "
        "background-color: rgba(4, 16, 37, 105); "
        "border-radius: 4px; padding: 1px 6px; "
        "font-family: 'Segoe UI'; font-size: 8pt;"
    )
    splash.showMessage(
        "PaperPack과 기존 색인을 읽는 중…",
        Qt.AlignLeft | Qt.AlignBottom,
        QColor("#ffffff"),
    )
    return splash
