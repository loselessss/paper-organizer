# 라이브러리의 paperpack에서 원본 PDF를 일괄 추출(PDF 환원)하는 다이얼로그
"""Restore original PDFs from library paperpacks into a user-chosen folder."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.core.paperpack import extract_paperpack_pdfs, iter_paperpacks


class _ExportWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, sources: list[Path], output_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self._sources = sources
        self._output_dir = output_dir

    def run(self) -> None:
        try:
            self.completed.emit(
                extract_paperpack_pdfs(self._sources, self._output_dir)
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class PdfExportDialog(QDialog):
    """Batch-extract embedded PDFs; paperpacks are always preserved."""

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._worker: _ExportWorker | None = None
        self.setWindowTitle("PDF 환원 (일괄 추출)")
        self.setMinimumWidth(560)

        _input_dir, library_root = controller.configured_paths()
        self._sources = sorted(iter_paperpacks(library_root))

        root = QVBoxLayout(self)
        note = QLabel(
            "라이브러리의 모든 paperpack에서 내장 PDF를 SHA-256 검증과 함께 추출합니다. "
            "원본 paperpack은 삭제되지 않으며 기존 출력 파일은 덮어쓰지 않습니다."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.count_label = QLabel(f"환원 대상 paperpack: {len(self._sources)}개")
        root.addWidget(self.count_label)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(str(Path.home() / "Documents" / "PaperOrganizer PDF"))
        browse_button = QPushButton("찾아보기…")
        browse_button.clicked.connect(self._browse_output)
        output_row.addWidget(QLabel("출력 폴더"))
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_button)
        root.addLayout(output_row)

        action_row = QHBoxLayout()
        self.export_button = QPushButton("PDF 환원 시작")
        self.export_button.clicked.connect(self._start_export)
        self.export_button.setEnabled(bool(self._sources))
        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.reject)
        action_row.addStretch(1)
        action_row.addWidget(self.export_button)
        action_row.addWidget(close_button)
        root.addLayout(action_row)

        self.status_label = QLabel(
            "" if self._sources else "라이브러리에 환원할 paperpack이 없습니다."
        )
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "PDF 출력 폴더 선택", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

    def _start_export(self) -> None:
        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.information(self, "출력 폴더 필요", "PDF를 저장할 폴더를 선택하세요.")
            return
        self.export_button.setEnabled(False)
        self.status_label.setText(
            f"{len(self._sources)}개 paperpack에서 PDF를 추출·검증하고 있습니다…"
        )
        worker = _ExportWorker(self._sources, Path(output_text), self)
        worker.completed.connect(self._export_completed)
        worker.failed.connect(self._export_failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _export_completed(self, result) -> None:
        self.status_label.setText(
            f"PDF {len(result.items)}개 환원 완료 · 출력 폴더: {self.output_edit.text()}"
        )

    def _export_failed(self, message: str) -> None:
        self.status_label.setText(f"환원 실패: {message}")
        QMessageBox.warning(self, "PDF 환원 실패", message)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.export_button.setEnabled(bool(self._sources))

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def reject(self) -> None:
        if self.is_busy():
            QMessageBox.information(
                self, "환원 진행 중", "PDF 추출이 끝난 뒤 창을 닫으세요."
            )
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.is_busy():
            event.ignore()
            return
        super().closeEvent(event)
