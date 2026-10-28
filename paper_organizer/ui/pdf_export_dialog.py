"""Restore original PDFs from library paperpacks into a user-chosen folder."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.core.paperpack import extract_paperpack_pdfs, iter_paperpacks
from paper_organizer.ui.dialog_utils import suppress_context_help_button


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

    def __init__(self, controller: LibraryWorkflowController, parent=None, *, selected_sources: list[Path] | None = None) -> None:
        super().__init__(parent)
        suppress_context_help_button(self)
        self._controller = controller
        self._worker: _ExportWorker | None = None
        self._output_dir: Path | None = None
        self.setWindowTitle("PDF 일괄 추출")
        self.setMinimumWidth(560)
        self.resize(660, 540)

        _input_dir, library_root = controller.configured_paths()
        sources = sorted(iter_paperpacks(library_root))
        selected = {Path(path).resolve() for path in selected_sources or []}
        titles = {entry.sidecar_path.resolve(): entry.metadata.title
                  for entry in controller.list_library()}

        root = QVBoxLayout(self)
        note = QLabel(
            "체크한 PaperPack에서 원본 PDF를 추출합니다. "
            "원본 PaperPack은 유지되며 기존 출력 파일은 덮어쓰지 않습니다."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.source_list = QListWidget()
        for path in sources:
            item = QListWidgetItem(titles.get(path.resolve()) or path.stem)
            item.setData(Qt.UserRole, path)
            item.setToolTip(str(path))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if not selected or path.resolve() in selected else Qt.Unchecked)
            self.source_list.addItem(item)
        root.addWidget(self.source_list, 1)
        selection_row = QHBoxLayout()
        self.select_all_button = QPushButton("전체 선택")
        self.select_none_button = QPushButton("선택 해제")
        self.select_all_button.clicked.connect(lambda: self._check_all(Qt.Checked))
        self.select_none_button.clicked.connect(lambda: self._check_all(Qt.Unchecked))
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.select_none_button)
        selection_row.addStretch(1)
        root.addLayout(selection_row)
        self.count_label = QLabel()
        root.addWidget(self.count_label)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(str(Path.home() / "Documents" / "PaperOrganizer PDF"))
        self.browse_button = QPushButton("찾아보기…")
        self.browse_button.clicked.connect(self._browse_output)
        output_row.addWidget(QLabel("출력 폴더"))
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.browse_button)
        root.addLayout(output_row)

        action_row = QHBoxLayout()
        self.export_button = QPushButton("PDF 일괄 추출 시작")
        self.export_button.clicked.connect(self._start_export)
        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.reject)
        action_row.addStretch(1)
        action_row.addWidget(self.export_button)
        action_row.addWidget(close_button)
        root.addLayout(action_row)

        self.status_label = QLabel(
            "" if sources else "라이브러리에 추출할 PaperPack이 없습니다."
        )
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.source_list.itemChanged.connect(self._selection_changed)
        self._selection_changed()

    def selected_sources(self) -> list[Path]:
        return [self.source_list.item(row).data(Qt.UserRole)
                for row in range(self.source_list.count())
                if self.source_list.item(row).checkState() == Qt.Checked]

    def _selection_changed(self) -> None:
        count = len(self.selected_sources())
        self.count_label.setText(f"추출 대상: {count} / {self.source_list.count()}개")
        self.export_button.setEnabled(bool(count) and self._worker is None)

    def _check_all(self, state) -> None:
        self.source_list.blockSignals(True)
        for row in range(self.source_list.count()):
            self.source_list.item(row).setCheckState(state)
        self.source_list.blockSignals(False)
        self._selection_changed()

    def _set_controls_enabled(self, enabled: bool) -> None:
        for control in (self.source_list, self.select_all_button, self.select_none_button,
                        self.output_edit, self.browse_button):
            control.setEnabled(enabled)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "PDF 출력 폴더 선택", self.output_edit.text()
        )
        if path:
            self.output_edit.setText(path)

    def _start_export(self) -> None:
        if self._worker is not None:
            return
        sources = self.selected_sources()
        if not sources:
            return
        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.information(self, "출력 폴더 필요", "PDF를 저장할 폴더를 선택하세요.")
            return
        self.export_button.setEnabled(False)
        self._set_controls_enabled(False)
        self._output_dir = Path(output_text)
        self.status_label.setText(
            f"{len(sources)}개 PaperPack에서 PDF를 추출·검증하고 있습니다…"
        )
        worker = _ExportWorker(sources, self._output_dir, self)
        worker.completed.connect(self._export_completed)
        worker.failed.connect(self._export_failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _export_completed(self, result) -> None:
        self.status_label.setText(
            f"PDF {len(result.items)}개 추출 완료 · 출력 폴더: {self._output_dir}"
        )

    def _export_failed(self, message: str) -> None:
        self.status_label.setText(f"추출 실패: {message}")
        QMessageBox.warning(self, "PDF 일괄 추출 실패", message)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_controls_enabled(True)
        self._selection_changed()

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def reject(self) -> None:
        if self.is_busy():
            QMessageBox.information(
                self, "추출 진행 중", "PDF 추출이 끝난 뒤 창을 닫으세요."
            )
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.is_busy():
            event.ignore()
            return
        super().closeEvent(event)
