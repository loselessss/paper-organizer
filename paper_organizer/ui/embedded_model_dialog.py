"""Dialog for app-managed GGUF model files."""

from __future__ import annotations

from threading import Event

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHeaderView,
)

from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.ui.dialog_utils import suppress_context_help_button


class _EmbeddedModelWorker(QThread):
    progress = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        controller: AiSettingsController,
        operation: str,
        model: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._operation = operation
        self._model = model
        self._cancel = Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            if self._operation == "download":
                result = self._controller.download_embedded_model(
                    self._model,
                    on_progress=self.progress.emit,
                    cancel=self._cancel,
                )
                self._controller.select_embedded_model(
                    self._model,
                    start_server=False,
                )
            elif self._operation == "select":
                result = self._controller.select_embedded_model(
                    self._model,
                    start_server=False,
                )
            elif self._operation == "delete":
                result = self._controller.delete_embedded_model(self._model)
            else:
                raise ValueError("알 수 없는 내장 모델 작업입니다.")
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class EmbeddedModelDialog(QDialog):
    model_selected = pyqtSignal(str)
    model_deleted = pyqtSignal(str, bool)

    def __init__(
        self,
        controller: AiSettingsController,
        parent=None,
        *,
        initial_model: str = "",
    ) -> None:
        super().__init__(parent)
        suppress_context_help_button(self)
        self._controller = controller
        self._preferred_model = initial_model.strip()
        self._snapshot = None
        self._worker: _EmbeddedModelWorker | None = None
        self._operation = ""
        self._operation_model = ""
        self.setWindowTitle("내장 로컬 AI 모델 관리")
        self.resize(820, 560)
        self.setMinimumSize(760, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        note = QLabel(
            "모델은 앱 전용 폴더의 GGUF 파일로 관리합니다. Ollama 설치나 공유 모델 "
            "저장소를 사용하지 않습니다."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        root.addWidget(note)

        table_header = QHBoxLayout()
        self.location_label = QLabel("")
        table_header.addWidget(self.location_label, 1)
        self.refresh_button = QPushButton("새로고침")
        self.refresh_button.clicked.connect(self.refresh)
        table_header.addWidget(self.refresh_button)
        root.addLayout(table_header)

        self.model_table = QTableWidget(0, 6)
        self.model_table.setHorizontalHeaderLabels(
            ["추천", "모델", "상태", "용량", "규모", "용도"]
        )
        self.model_table.setAlternatingRowColors(True)
        self.model_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.model_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.model_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.itemSelectionChanged.connect(self._selection_changed)
        header = self.model_table.horizontalHeader()
        for column in range(5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        root.addWidget(self.model_table, 1)

        self.detail_label = QLabel("모델을 선택하세요.")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMaximumHeight(
            self.detail_label.fontMetrics().lineSpacing() * 3 + 10
        )
        root.addWidget(self.detail_label)

        self.progress_status = QLabel("모델 작업 대기 중")
        self.progress_status.setWordWrap(True)
        root.addWidget(self.progress_status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("대기 중")
        self.progress.setAlignment(Qt.AlignCenter)
        root.addWidget(self.progress)

        actions = QHBoxLayout()
        self.select_button = QPushButton("선택")
        self.select_button.clicked.connect(self._select_selected)
        actions.addWidget(self.select_button)
        self.download_button = QPushButton("다운로드 후 선택")
        self.download_button.clicked.connect(self._download_selected)
        actions.addWidget(self.download_button)
        self.cancel_button = QPushButton("다운로드 취소")
        self.cancel_button.clicked.connect(self._cancel_operation)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        self.delete_button = QPushButton("선택한 모델 제거")
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_button)
        root.addLayout(actions)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._update_actions()

    def refresh(self) -> None:
        try:
            self._snapshot = self._controller.embedded_model_snapshot()
        except Exception as exc:
            self._snapshot = None
            self.progress_status.setText(f"모델 목록을 불러오지 못했습니다: {exc}")
            self.model_table.setRowCount(0)
            self._update_actions()
            return
        self.location_label.setText(
            f"모델 폴더 {self._snapshot.disk_path} · 여유 공간 "
            f"{self._snapshot.disk_free_gb:g}GB"
        )
        self.model_table.setSortingEnabled(False)
        self.model_table.setRowCount(len(self._snapshot.entries))
        preferred_row = 0
        for row, entry in enumerate(self._snapshot.entries):
            if _same_model(entry.model_id, self._preferred_model):
                preferred_row = row
            status = "설치됨" if entry.installed else "미설치"
            if not entry.installed and not entry.download_available:
                status = "주소 없음"
            values = [
                "추천",
                entry.label,
                status,
                (
                    f"{entry.installed_size_gb:g}GB"
                    if entry.installed
                    else f"예상 {entry.estimated_download_gb:g}GB"
                ),
                entry.parameter_size,
                entry.usage_guidance,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, entry.model_id)
                self.model_table.setItem(row, column, item)
        self.model_table.setSortingEnabled(True)
        if self._snapshot.entries:
            self.model_table.selectRow(preferred_row)
        self.progress_status.setText("모델 작업 대기 중")
        self.progress.setValue(0)
        self.progress.setFormat("대기 중")
        self._update_actions()

    def _selected_model(self) -> str:
        items = self.model_table.selectedItems()
        if not items:
            return ""
        return str(items[0].data(Qt.UserRole) or "")

    def _selected_entry(self):
        model = self._selected_model()
        if not model or self._snapshot is None:
            return None
        for entry in self._snapshot.entries:
            if _same_model(entry.model_id, model):
                return entry
        return None

    def _selection_changed(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.detail_label.setText("모델을 선택하세요.")
        else:
            availability = (
                "직접 다운로드 가능"
                if entry.download_available
                else "직접 다운로드 주소 미등록"
            )
            self.detail_label.setText(
                f"{entry.model_id} · {availability} · {entry.usage_guidance}"
            )
        self._update_actions()

    def _select_selected(self) -> None:
        model = self._selected_model()
        if model:
            self._start_operation("select", model)

    def _download_selected(self) -> None:
        model = self._selected_model()
        if not model:
            return
        try:
            plan = self._controller.plan_embedded_model_download(model)
        except Exception as exc:
            QMessageBox.warning(self, "모델 다운로드 불가", str(exc))
            return
        if not plan.can_download:
            QMessageBox.information(self, "모델 다운로드 불가", plan.reason)
            return
        if QMessageBox.question(
            self,
            "모델 다운로드",
            f"{plan.label} 모델을 다운로드할까요?\n\n"
            f"예상 {plan.estimated_download_gb:g}GB · 필요 여유 "
            f"{plan.required_free_gb:g}GB",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        self._start_operation("download", model)

    def _delete_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None or not entry.installed:
            return
        if QMessageBox.warning(
            self,
            "모델 제거",
            f"{entry.label} 모델 파일을 앱 모델 폴더에서 제거할까요?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        self._start_operation("delete", entry.model_id)

    def _start_operation(self, operation: str, model: str) -> None:
        self._operation = operation
        self._operation_model = model
        self._worker = _EmbeddedModelWorker(
            self._controller,
            operation,
            model,
            self,
        )
        self._worker.progress.connect(self._progress_changed)
        self._worker.completed.connect(self._operation_completed)
        self._worker.failed.connect(self._operation_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()
        self.progress.setRange(0, 0 if operation == "download" else 100)
        self.progress.setFormat("진행 중")
        self.progress_status.setText("모델 작업 진행 중")
        self._update_actions()

    def _progress_changed(self, progress) -> None:
        total = getattr(progress, "total_bytes", None)
        received = int(getattr(progress, "received_bytes", 0) or 0)
        if total:
            percent = max(0, min(100, int(received * 100 / total)))
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
            self.progress.setFormat(f"{percent}%")
            self.progress_status.setText(
                f"다운로드 중 · {_format_bytes(received)} / {_format_bytes(total)}"
            )
        else:
            self.progress.setRange(0, 0)
            self.progress_status.setText("다운로드 중")

    def _operation_completed(self, result) -> None:
        operation = self._operation
        model = self._operation_model
        self._worker = None
        self._operation = ""
        self._operation_model = ""
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("완료")
        if operation in {"download", "select"}:
            self.model_selected.emit(model)
            self.progress_status.setText("모델을 선택했습니다.")
        elif operation == "delete":
            self.model_deleted.emit(model, bool(result))
            self.progress_status.setText("선택한 모델을 제거했습니다.")
        self.refresh()

    def _operation_failed(self, message: str) -> None:
        self._worker = None
        self._operation = ""
        self._operation_model = ""
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("실패")
        self.progress_status.setText(f"모델 작업 실패: {message}")
        QMessageBox.warning(self, "모델 작업 실패", message)
        self._update_actions()

    def _cancel_operation(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.progress_status.setText("다운로드 취소 요청 중")

    def _update_actions(self) -> None:
        busy = self._worker is not None and self._worker.isRunning()
        entry = self._selected_entry()
        self.refresh_button.setEnabled(not busy)
        self.model_table.setEnabled(not busy)
        self.select_button.setEnabled(bool(entry and entry.installed) and not busy)
        self.download_button.setEnabled(
            bool(entry and not entry.installed and entry.download_available)
            and not busy
        )
        self.delete_button.setEnabled(bool(entry and entry.installed) and not busy)
        self.cancel_button.setEnabled(busy and self._operation == "download")

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self,
                "모델 작업 중",
                "모델 작업이 끝난 뒤 창을 닫으세요.",
            )
            return
        super().reject()


def _same_model(left: str, right: str) -> bool:
    return (
        left.strip().casefold().removesuffix(":latest")
        == right.strip().casefold().removesuffix(":latest")
    )


def _format_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / (1024 ** 3):.2f}GB"
    if value >= 1024**2:
        return f"{value / (1024 ** 2):.1f}MB"
    return f"{value / 1024:.0f}KB"
