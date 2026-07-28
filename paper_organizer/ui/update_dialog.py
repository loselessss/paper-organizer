"""GitHub Releases update check and download user interface."""

from __future__ import annotations

from pathlib import Path
from threading import Event

from PyQt5.QtCore import QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from paper_organizer.application.update_service import (
    AvailableUpdate,
    GitHubUpdateService,
    UpdateCancelled,
    UpdateDownloadProgress,
)


def _size_text(size: int) -> str:
    if size <= 0:
        return "크기 정보 없음"
    return f"{size / (1024 * 1024):.1f} MB"


class UpdateCheckWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, service: GitHubUpdateService, parent=None) -> None:
        super().__init__(parent)
        self._service = service

    def run(self) -> None:
        try:
            self.completed.emit(self._service.check())
        except Exception as exc:
            self.failed.emit(str(exc))


class _UpdateDownloadWorker(QThread):
    progress = pyqtSignal(object)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        service: GitHubUpdateService,
        update: AvailableUpdate,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._update = update
        self._cancel = Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            path = self._service.download(
                self._update,
                progress=self.progress.emit,
                cancel=self._cancel,
            )
            self.completed.emit(str(path))
        except UpdateCancelled as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateDialog(QDialog):
    install_requested = pyqtSignal(object)
    skip_requested = pyqtSignal(str)

    def __init__(
        self,
        service: GitHubUpdateService,
        update: AvailableUpdate,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._update = update
        self._worker: _UpdateDownloadWorker | None = None
        self.setWindowTitle("Paper Organizer 업데이트")
        self.setMinimumSize(620, 480)

        layout = QVBoxLayout(self)
        title = QLabel(
            f"<h3>Paper Organizer {update.version} 업데이트가 있습니다.</h3>"
        )
        layout.addWidget(title)

        form = QFormLayout()
        form.addRow("현재 버전", QLabel(service.current_version))
        form.addRow("새 버전", QLabel(update.version))
        form.addRow(
            "설치파일",
            QLabel(
                (
                    f"{update.asset.name} ({_size_text(update.asset.size)})"
                    if update.asset
                    else "등록 대기 중"
                )
            ),
        )
        layout.addLayout(form)

        layout.addWidget(QLabel("변경 내용"))
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(False)
        self.notes.setPlainText(update.release_notes or "변경 기록이 없습니다.")
        layout.addWidget(self.notes, 1)
        choice_note = QLabel(
            "설치파일은 한 버전 전체 단위입니다. 지금 설치하거나, 나중에 다시 "
            "알림을 받거나, 이 버전만 건너뛸 수 있습니다."
        )
        choice_note.setWordWrap(True)
        choice_note.setStyleSheet("color: #666;")
        layout.addWidget(choice_note)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.button(QDialogButtonBox.Close).setText("나중에")
        self.buttons.rejected.connect(self.reject)
        self.release_button = QPushButton("릴리스 페이지")
        self.release_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(update.release_url))
        )
        self.buttons.addButton(
            self.release_button, QDialogButtonBox.ActionRole
        )
        self.skip_button = QPushButton("이 버전 건너뛰기")
        self.skip_button.clicked.connect(self._skip_version)
        self.buttons.addButton(self.skip_button, QDialogButtonBox.ActionRole)
        self.install_button = QPushButton("다운로드 후 설치")
        self.install_button.clicked.connect(self._start_download)
        self.buttons.addButton(
            self.install_button, QDialogButtonBox.AcceptRole
        )
        layout.addWidget(self.buttons)

        if update.asset is None:
            self.install_button.setEnabled(False)
            self.status_label.setText(
                "이 릴리스에는 아직 Windows 설치파일이 없습니다. "
                "릴리스 페이지에서 다시 확인하세요."
            )
        elif not update.asset.sha256:
            self.install_button.setEnabled(False)
            self.status_label.setText(
                "설치파일 무결성 정보가 없어 앱 안에서는 자동 설치하지 않습니다."
            )

    def _skip_version(self) -> None:
        if QMessageBox.question(
            self,
            "업데이트 건너뛰기",
            f"v{self._update.version} 알림을 더 이상 자동으로 표시하지 않을까요?\n"
            "‘업데이트 확인’ 메뉴에서는 언제든 다시 확인할 수 있습니다.",
        ) != QMessageBox.Yes:
            return
        self.skip_requested.emit(self._update.version)
        self.accept()

    def _start_download(self) -> None:
        if self._worker is not None:
            return
        self.install_button.setEnabled(False)
        self.release_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.progress_bar.show()
        self.status_label.setText("업데이트 설치파일을 다운로드하는 중입니다…")
        worker = _UpdateDownloadWorker(self._service, self._update, self)
        worker.progress.connect(self._download_progress)
        worker.completed.connect(self._download_completed)
        worker.failed.connect(self._download_failed)
        worker.finished.connect(self._download_finished)
        self._worker = worker
        worker.start()

    def _download_progress(self, progress: UpdateDownloadProgress) -> None:
        if progress.total_bytes > 0:
            percent = min(
                100,
                round(progress.completed_bytes * 100 / progress.total_bytes),
            )
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)
        else:
            self.progress_bar.setRange(0, 0)
        completed_mb = progress.completed_bytes / (1024 * 1024)
        total = (
            f" / {progress.total_bytes / (1024 * 1024):.1f} MB"
            if progress.total_bytes
            else ""
        )
        speed = progress.bytes_per_second / (1024 * 1024)
        self.status_label.setText(
            f"{completed_mb:.1f} MB{total} · {speed:.1f} MB/s"
        )

    def _download_completed(self, path: str) -> None:
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status_label.setText(
            "다운로드와 SHA-256 검증을 마쳤습니다. 설치를 시작합니다."
        )
        self.install_requested.emit(Path(path))
        self.accept()

    def _download_failed(self, message: str) -> None:
        self.status_label.setText(message)
        if "취소" not in message:
            QMessageBox.warning(self, "업데이트 다운로드 실패", message)

    def _download_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.release_button.setEnabled(True)
        self.skip_button.setEnabled(True)
        if self._update.asset is not None and self._update.asset.sha256:
            self.install_button.setEnabled(True)

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.status_label.setText("다운로드를 취소하는 중입니다…")
            return
        super().reject()
