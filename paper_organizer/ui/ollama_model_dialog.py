"""Responsive Ollama model manager with explicit download and deletion actions."""

from __future__ import annotations

from threading import Event
import time

from PyQt5.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.infra.ollama_installer import (
    OLLAMA_DOWNLOAD_URL,
    ensure_runtime,
    inspect_runtime,
)
from paper_organizer.infra.ollama_models import OllamaOperationCancelled


class _RuntimeSetupWorker(QThread):
    """Install or start Ollama off the UI thread; install needs consent."""

    completed = pyqtSignal(object)

    def __init__(self, allow_install: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._allow_install = allow_install

    def run(self) -> None:
        try:
            self.completed.emit(ensure_runtime(allow_install=self._allow_install))
        except Exception as exc:  # 설치 도구 실패가 앱을 멈추지 않게 한다
            from paper_organizer.infra.ollama_installer import (
                OllamaRuntimeState,
                OllamaSetupResult,
            )

            self.completed.emit(
                OllamaSetupResult(
                    False,
                    OllamaRuntimeState(False, False, "", message=str(exc)),
                    f"Ollama 준비에 실패했습니다: {exc}",
                    needs_manual_download=True,
                )
            )


class _ModelOperationWorker(QThread):
    progress = pyqtSignal(object)
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal(str)

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
            if self._operation == "refresh":
                result = self._controller.ollama_model_snapshot()
            elif self._operation == "install":
                result = self._controller.install_ollama_model(
                    self._model,
                    on_progress=self.progress.emit,
                    cancel=self._cancel,
                )
            elif self._operation == "verify":
                result = self._controller.verify_installed_ollama_model(self._model)
            elif self._operation == "delete":
                result = self._controller.delete_ollama_model(self._model)
            else:
                raise ValueError("알 수 없는 모델 관리 작업입니다.")
            self.completed.emit(result)
        except OllamaOperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))


class OllamaModelDialog(QDialog):
    model_verified = pyqtSignal(str)
    model_deleted = pyqtSignal(str, bool)

    def __init__(
        self,
        controller: AiSettingsController,
        parent=None,
        *,
        initial_model: str = "",
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._preferred_model = initial_model.strip()
        self._worker: _ModelOperationWorker | None = None
        self._operation = ""
        self._operation_model = ""
        self._snapshot = None
        self._refresh_after_operation = False
        self._runtime_worker: _RuntimeSetupWorker | None = None
        self._download_last_at = 0.0
        self._download_last_bytes = 0
        self._download_speed_bps = 0.0
        self._download_highest_percent = 0
        self._runtime_elapsed_seconds = 0
        self._runtime_installing = False
        self._runtime_timer = QTimer(self)
        self._runtime_timer.setInterval(1000)
        self._runtime_timer.timeout.connect(self._runtime_tick)
        self.setWindowTitle("Ollama 모델 관리")
        self.resize(820, 620)
        self.setMinimumSize(760, 560)

        root = QVBoxLayout(self)
        warning = QLabel(
            "Ollama 모델 저장소는 다른 앱과 공유될 수 있습니다. Paper Organizer를 "
            "제거해도 모델은 자동 삭제하지 않으며, 아래 삭제 버튼을 누르고 확인한 "
            "모델만 삭제합니다."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a4b00;")
        root.addWidget(warning)

        self.runtime_status = QLabel("아직 Ollama 상태를 확인하지 않았습니다.")
        self.runtime_status.setWordWrap(True)
        root.addWidget(self.runtime_status)

        runtime_row = QHBoxLayout()
        self.setup_runtime_button = QPushButton("Ollama 설치 및 실행")
        self.setup_runtime_button.setToolTip(
            "Ollama가 없으면 winget으로 설치하고, 설치되어 있으면 실행만 합니다."
        )
        self.setup_runtime_button.clicked.connect(self._setup_runtime)
        self.setup_runtime_button.setVisible(False)
        runtime_row.addWidget(self.setup_runtime_button)
        self.open_download_button = QPushButton("Ollama 공식 다운로드")
        self.open_download_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(OLLAMA_DOWNLOAD_URL))
        )
        runtime_row.addWidget(self.open_download_button)
        runtime_row.addStretch(1)
        root.addLayout(runtime_row)

        selection_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.refresh_button = QPushButton("새로고침")
        selection_row.addWidget(self.model_combo, 1)
        selection_row.addWidget(self.refresh_button)
        root.addLayout(selection_row)

        self.model_detail = QLabel("모델을 선택하세요.")
        self.model_detail.setWordWrap(True)
        root.addWidget(self.model_detail)

        installed_label = QLabel(
            "설치된 모델 — 항목을 선택하면 바로 삭제할 수 있습니다."
        )
        root.addWidget(installed_label)
        self.installed_models = QListWidget()
        self.installed_models.setAlternatingRowColors(True)
        self.installed_models.setSelectionMode(QListWidget.SingleSelection)
        self.installed_models.setToolTip(
            "삭제할 모델을 이 목록에서 직접 선택하세요."
        )
        root.addWidget(self.installed_models, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("대기 중")
        self.progress.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.progress.setMinimumHeight(24)
        self.progress.setStyleSheet(
            "QProgressBar { text-align: left; padding-left: 8px; }"
        )
        root.addWidget(self.progress)

        action_row = QHBoxLayout()
        self.install_button = QPushButton("다운로드 후 선택")
        self.cancel_button = QPushButton("다운로드 취소")
        self.delete_button = QPushButton("목록에서 선택한 모델 삭제")
        self.cancel_button.setEnabled(False)
        action_row.addWidget(self.install_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch(1)
        action_row.addWidget(self.delete_button)
        root.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.refresh_button.clicked.connect(self.refresh)
        self.model_combo.currentIndexChanged.connect(self._selection_changed)
        self.installed_models.currentItemChanged.connect(
            self._installed_selection_changed
        )
        self.install_button.clicked.connect(self._install)
        self.cancel_button.clicked.connect(self._cancel_download)
        self.delete_button.clicked.connect(self._delete)
        self._update_actions()
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowContextHelpButtonHint
        )

    def refresh(self) -> None:
        if self._busy():
            return
        self.runtime_status.setText("Ollama와 모델 디스크를 확인하는 중…")
        self._start_worker("refresh")

    def _start_worker(self, operation: str, model: str = "") -> None:
        self._operation = operation
        self._operation_model = model
        worker = _ModelOperationWorker(self._controller, operation, model, self)
        worker.progress.connect(self._progress_changed)
        worker.completed.connect(self._operation_completed)
        worker.failed.connect(self._operation_failed)
        worker.cancelled.connect(self._operation_cancelled)
        worker.finished.connect(self._operation_finished)
        self._worker = worker
        self._update_actions()
        worker.start()

    def _operation_completed(self, result) -> None:
        if self._operation == "refresh":
            self._apply_snapshot(result)
            return
        if self._operation in {"install", "verify"}:
            model = (
                result.verification.model
                if self._operation == "install"
                else result.model
            )
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            self.progress.setFormat(
                "설치 및 JSON 응답 검증 완료"
                if self._operation == "install"
                else "JSON 응답 검증 완료"
            )
            self._controller.select_ollama_model(model.name)
            self.model_verified.emit(model.name)
            installed_now = self._operation == "install"
            QMessageBox.information(
                self,
                "모델 설치 완료" if installed_now else "모델 검증 완료",
                f"{model.name} {'설치와 검증을 마쳤습니다' if installed_now else '검증을 마쳤습니다'}.\n"
                "활성 Ollama 모델로 선택하고 설정에 저장했습니다.",
            )
            self._refresh_after_operation = self._operation == "install"
            return
        if self._operation == "delete":
            cleared = bool(result)
            self.model_deleted.emit(self._operation_model, cleared)
            suffix = " 활성 모델 선택도 비웠습니다." if cleared else ""
            QMessageBox.information(
                self,
                "모델 삭제 완료",
                "선택한 Ollama 모델을 삭제했습니다." + suffix,
            )
            self._refresh_after_operation = True

    def _operation_failed(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("작업 실패")
        QMessageBox.warning(self, "Ollama 모델 작업 실패", message)

    def _operation_cancelled(self, message: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("다운로드 취소됨")
        self.runtime_status.setText(message)
        self._refresh_after_operation = True

    def _operation_finished(self) -> None:
        self._worker = None
        self._operation = ""
        self._operation_model = ""
        self._update_actions()
        if self._refresh_after_operation:
            self._refresh_after_operation = False
            QTimer.singleShot(0, self.refresh)

    def _setup_runtime(self) -> None:
        """Ask before installing, then install or start Ollama in a worker."""

        state = inspect_runtime()
        allow_install = False
        if not state.installed:
            if not state.can_install_with_winget:
                QMessageBox.information(
                    self,
                    "Ollama 설치 필요",
                    "이 PC에서는 winget을 쓸 수 없어 자동 설치할 수 없습니다.\n"
                    f"{OLLAMA_DOWNLOAD_URL} 에서 설치 프로그램을 내려받아 설치한 뒤 "
                    "새로고침하세요.",
                )
                return
            if QMessageBox.question(
                self,
                "Ollama 설치",
                "로컬 AI를 쓰려면 Ollama 런타임이 필요합니다.\n"
                "winget으로 Ollama를 설치할까요? 모델은 이 단계에서 받지 않습니다.",
            ) != QMessageBox.Yes:
                return
            allow_install = True
        self.setup_runtime_button.setEnabled(False)
        self.runtime_status.setText(
            "Ollama를 설치하고 실행하는 중…" if allow_install else "Ollama를 실행하는 중…"
        )
        self._runtime_elapsed_seconds = 0
        self._runtime_installing = allow_install
        self._runtime_timer.start()
        worker = _RuntimeSetupWorker(allow_install, self)
        worker.completed.connect(self._runtime_setup_finished)
        worker.finished.connect(worker.deleteLater)
        self._runtime_worker = worker
        worker.start()

    def _runtime_setup_finished(self, result) -> None:
        self._runtime_timer.stop()
        self._runtime_worker = None
        self.setup_runtime_button.setEnabled(True)
        self.runtime_status.setText(result.message)
        if result.ok:
            QTimer.singleShot(0, self.refresh)
            return
        if result.needs_manual_download:
            QMessageBox.information(
                self,
                "Ollama 직접 설치",
                f"{result.message}\n\n설치 페이지: {OLLAMA_DOWNLOAD_URL}",
            )
        else:
            QMessageBox.warning(self, "Ollama 준비 실패", result.message)

    def _runtime_tick(self) -> None:
        self._runtime_elapsed_seconds += 1
        elapsed = self._runtime_elapsed_seconds
        if not self._runtime_installing:
            self.runtime_status.setText(
                f"Ollama 서버가 응답하기를 기다리는 중 · {elapsed}초 경과"
            )
            return
        self.runtime_status.setText(
            f"Ollama 자동 설치 요청 중 · {elapsed}초 경과\n"
            "winget 다운로드는 작업 관리자에서 Delivery Optimization/Service의 "
            "네트워크 사용량으로 표시될 수 있습니다. 3분 안에 끝나지 않으면 자동 "
            "설치를 중단하고 공식 다운로드를 안내합니다."
        )

    def _apply_snapshot(self, snapshot) -> None:
        self._snapshot = snapshot
        if snapshot.reachable:
            self.runtime_status.setText(
                f"Ollama {snapshot.version} · 모델 저장 위치 {snapshot.disk_path} · "
                f"여유 공간 {snapshot.disk_free_gb:g}GB"
            )
        else:
            detail = f" ({snapshot.error})" if snapshot.error else ""
            self.runtime_status.setText(
                "Ollama에 연결할 수 없습니다. 아래 버튼으로 설치·실행할 수 있습니다."
                + detail
            )
        self.setup_runtime_button.setVisible(not snapshot.reachable)
        selected = self._preferred_model or self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for entry in snapshot.entries:
            if not entry.selectable:
                continue
            state = "설치됨" if entry.installed else "미설치"
            size = (
                f"실제 {entry.installed_size_gb:g}GB"
                if entry.installed
                else f"예상 {entry.estimated_download_gb:g}GB"
                if entry.estimated_download_gb is not None
                else "크기 미상"
            )
            self.model_combo.addItem(
                f"{entry.label} — {state}, {size}", entry.model_id
            )
        selected_found = False
        if selected:
            index = self.model_combo.findData(selected)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
                selected_found = True
                self._preferred_model = ""
        if not selected_found:
            first_installed = next(
                (
                    index
                    for index, entry in enumerate(snapshot.entries)
                    if entry.installed
                ),
                0,
            )
            if self.model_combo.count():
                self.model_combo.setCurrentIndex(first_installed)
        self.model_combo.blockSignals(False)
        self.installed_models.blockSignals(True)
        self.installed_models.clear()
        for entry in snapshot.entries:
            if not entry.installed:
                continue
            owner = " · 앱에서 받은 모델" if entry.managed_by_app else " · 공유/기존 모델"
            selection = " · 12B 이상 선택 제외" if not entry.selectable else ""
            details = " ".join(
                value for value in (entry.parameter_size, entry.quantization) if value
            )
            item = QListWidgetItem(
                f"{entry.model_id} — {entry.installed_size_gb:g}GB"
                f"{f' · {details}' if details else ''}{owner}{selection}"
            )
            item.setData(Qt.UserRole, entry.model_id)
            self.installed_models.addItem(item)
        self.installed_models.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.model_detail.setText("모델을 선택하세요.")
        elif entry.installed:
            owner = "앱에서 다운로드" if entry.managed_by_app else "기존/공유 모델"
            self.model_detail.setText(
                f"설치 크기 {entry.installed_size_gb:g}GB · {owner} · "
                f"{entry.parameter_size or '파라미터 미상'} · "
                f"{entry.quantization or '양자화 미상'}"
            )
        else:
            required = (entry.estimated_download_gb or 0) * 1.5 + 2.0
            self.model_detail.setText(
                f"예상 다운로드 {entry.estimated_download_gb:g}GB · "
                f"안전 여유 필요 약 {required:.1f}GB"
            )
        self._select_installed_model(entry.model_id if entry and entry.installed else "")
        self._update_actions()

    def _installed_selection_changed(self, current, _previous=None) -> None:
        if current is None:
            self._update_actions()
            return
        model = str(current.data(Qt.UserRole) or "")
        entry = self._entry_for_model(model)
        if entry is None:
            self._update_actions()
            return
        index = self.model_combo.findData(entry.model_id)
        if index >= 0 and index != self.model_combo.currentIndex():
            self.model_combo.setCurrentIndex(index)
            return
        owner = "앱에서 다운로드" if entry.managed_by_app else "기존/공유 모델"
        selection = " · 분석 모델 선택 제외" if not entry.selectable else ""
        self.model_detail.setText(
            f"설치 크기 {entry.installed_size_gb:g}GB · {owner} · "
            f"{entry.parameter_size or '파라미터 미상'} · "
            f"{entry.quantization or '양자화 미상'}{selection}"
        )
        self._update_actions()

    def _install(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if entry.installed:
            if QMessageBox.question(
                self,
                "설치 모델 검증",
                f"{entry.model_id}의 짧은 JSON 응답을 검증한 뒤 선택할까요?",
            ) != QMessageBox.Yes:
                return
            self.progress.setRange(0, 0)
            self.progress.setFormat("설치 모델을 검증하는 중…")
            self._start_worker("verify", entry.model_id)
            return
        if entry.estimated_download_gb is None:
            return
        required = entry.estimated_download_gb * 1.5 + 2.0
        if self._snapshot.disk_free_gb < required:
            QMessageBox.warning(
                self,
                "디스크 공간 부족",
                f"현재 {self._snapshot.disk_free_gb:g}GB, 안전 설치에는 "
                f"약 {required:.1f}GB가 필요합니다.",
            )
            return
        if QMessageBox.question(
            self,
            "Ollama 모델 다운로드",
            f"{entry.model_id}을(를) 다운로드할까요?\n"
            f"예상 다운로드 {entry.estimated_download_gb:g}GB\n"
            "완료 후 설치 목록과 짧은 JSON 응답을 검증합니다.",
        ) != QMessageBox.Yes:
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("다운로드 준비 중…")
        self._download_last_at = time.monotonic()
        self._download_last_bytes = 0
        self._download_speed_bps = 0.0
        self._download_highest_percent = 0
        self._start_worker("install", entry.model_id)

    def _delete(self) -> None:
        entry = self._selected_installed_entry()
        if entry is None:
            return
        if QMessageBox.warning(
            self,
            "공유 Ollama 모델 삭제",
            f"{entry.model_id} ({entry.installed_size_gb:g}GB)을(를) 삭제할까요?\n\n"
            "이 모델은 다른 프로그램에서도 사용 중일 수 있습니다. "
            "Paper Organizer 삭제 프로그램은 이 작업을 대신 수행하지 않습니다.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        self.progress.setRange(0, 0)
        self.progress.setFormat("삭제 및 설치 목록 확인 중…")
        self._start_worker("delete", entry.model_id)

    def _cancel_download(self) -> None:
        if self._worker is not None and self._operation == "install":
            self.cancel_button.setEnabled(False)
            self.progress.setFormat("안전한 지점에서 취소하는 중…")
            self._worker.request_cancel()

    def _progress_changed(self, progress) -> None:
        now = time.monotonic()
        if progress.completed_bytes < self._download_last_bytes:
            self._download_last_bytes = 0
            self._download_last_at = now
        elapsed = now - self._download_last_at
        transferred = progress.completed_bytes - self._download_last_bytes
        if elapsed > 0.2 and transferred >= 0:
            current_speed = transferred / elapsed
            self._download_speed_bps = (
                current_speed
                if self._download_speed_bps <= 0
                else self._download_speed_bps * 0.7 + current_speed * 0.3
            )
            self._download_last_at = now
            self._download_last_bytes = progress.completed_bytes
        detail = _download_detail(
            progress.completed_bytes,
            progress.total_bytes,
            self._download_speed_bps,
        )
        if progress.percent is None:
            self.progress.setRange(0, 100)
            self.progress.setValue(self._download_highest_percent)
            self.progress.setFormat(f"{progress.status}{detail}")
        else:
            if progress.status.casefold() == "success":
                self._download_highest_percent = 100
            else:
                self._download_highest_percent = min(
                    99,
                    max(self._download_highest_percent, progress.percent),
                )
            self.progress.setRange(0, 100)
            self.progress.setValue(self._download_highest_percent)
            self.progress.setFormat(
                f"{progress.status} — {self._download_highest_percent}%{detail}"
            )

    def _selected_entry(self):
        if self._snapshot is None:
            return None
        model = self.model_combo.currentData()
        return next(
            (entry for entry in self._snapshot.entries if entry.model_id == model),
            None,
        )

    def _selected_installed_entry(self):
        item = self.installed_models.currentItem()
        if item is None:
            return None
        entry = self._entry_for_model(str(item.data(Qt.UserRole) or ""))
        return entry if entry is not None and entry.installed else None

    def _entry_for_model(self, model: str):
        if self._snapshot is None:
            return None
        key = model.strip().casefold().removesuffix(":latest")
        return next(
            (
                entry
                for entry in self._snapshot.entries
                if entry.model_id.casefold().removesuffix(":latest") == key
            ),
            None,
        )

    def _select_installed_model(self, model: str) -> None:
        key = model.strip().casefold().removesuffix(":latest")
        target = None
        for row in range(self.installed_models.count()):
            item = self.installed_models.item(row)
            item_key = str(item.data(Qt.UserRole) or "").casefold().removesuffix(
                ":latest"
            )
            if item_key == key:
                target = item
                break
        if target is self.installed_models.currentItem():
            return
        self.installed_models.blockSignals(True)
        self.installed_models.setCurrentItem(target)
        if target is None:
            self.installed_models.clearSelection()
        self.installed_models.blockSignals(False)

    def _busy(self) -> bool:
        return self._worker is not None

    def _update_actions(self) -> None:
        busy = self._busy()
        entry = self._selected_entry()
        reachable = bool(self._snapshot and self._snapshot.reachable)
        self.refresh_button.setEnabled(not busy)
        self.model_combo.setEnabled(not busy and self.model_combo.count() > 0)
        self.install_button.setEnabled(
            not busy
            and reachable
            and entry is not None
            and (entry.installed or entry.estimated_download_gb is not None)
        )
        self.install_button.setText(
            "검증 후 선택"
            if entry is not None and entry.installed
            else "다운로드 후 선택"
        )
        self.delete_button.setEnabled(
            not busy and reachable and self._selected_installed_entry() is not None
        )
        self.cancel_button.setEnabled(busy and self._operation == "install")

    def reject(self) -> None:
        if self._busy():
            QMessageBox.information(
                self,
                "모델 작업 진행 중",
                "다운로드를 취소하거나 현재 작업이 끝난 뒤 창을 닫으세요.",
            )
            return
        super().reject()


def _download_detail(completed: int, total: int, speed_bps: float) -> str:
    parts: list[str] = []
    if completed > 0:
        transferred = f"{completed / (1024 ** 2):.1f}MB"
        if total > 0:
            transferred += f"/{total / (1024 ** 3):.2f}GB"
        parts.append(transferred)
    if speed_bps > 0:
        parts.append(f"{speed_bps / (1024 ** 2):.1f}MB/s")
        if total > completed:
            eta = max(0, round((total - completed) / speed_bps))
            parts.append(f"약 {eta // 60}분 {eta % 60}초 남음")
    return f" · {' · '.join(parts)}" if parts else ""
