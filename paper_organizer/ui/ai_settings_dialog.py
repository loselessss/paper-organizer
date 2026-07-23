"""PyQt dialog for provider, model, consent, throughput and credential controls."""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.ui.ollama_model_dialog import OllamaModelDialog


class _HardwareScanWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, controller: AiSettingsController, profile: str, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._profile = profile

    def run(self) -> None:
        try:
            self.completed.emit(self._controller.scan_local_ai(self._profile))
        except Exception as exc:
            self.failed.emit(str(exc))


class AiSettingsDialog(QDialog):
    def __init__(self, controller: AiSettingsController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("요약 AI 설정")
        self.resize(720, 690)
        self._scan_worker: _HardwareScanWorker | None = None
        self._recommended_model = ""

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.provider_combo = QComboBox()
        self.model_edit = QLineEdit()
        self.key_status = QLabel()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("새 API 키를 입력할 때만 사용")
        self.key_save_button = QPushButton("키 저장/교체")
        self.key_delete_button = QPushButton("키 삭제")
        key_buttons = QWidget()
        key_layout = QHBoxLayout(key_buttons)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_edit, 1)
        key_layout.addWidget(self.key_save_button)
        key_layout.addWidget(self.key_delete_button)

        self.consent_check = QCheckBox("이 제공자로 논문 텍스트 전송을 계속 허용")
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("보수적", "conservative")
        self.profile_combo.addItem("표준", "standard")
        self.profile_combo.addItem("대량 처리", "high_throughput")
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 16)
        self.budget_spin = QDoubleSpinBox()
        self.budget_spin.setRange(0, 1_000_000)
        self.budget_spin.setDecimals(2)
        self.budget_spin.setPrefix("US$ ")
        self.budget_spin.setSpecialValueText("앱 자체 제한 없음")

        form.addRow("제공자", self.provider_combo)
        form.addRow("모델", self.model_edit)
        form.addRow("키 상태", self.key_status)
        form.addRow("API 키", key_buttons)
        form.addRow("클라우드 동의", self.consent_check)
        form.addRow("클라우드 처리량", self.profile_combo)
        form.addRow("최대 병렬 요청", self.parallel_spin)
        form.addRow("월간 앱 비용 한도", self.budget_spin)
        root.addLayout(form)

        local_group = QGroupBox("로컬 AI 사양 및 모델 추천")
        local_layout = QVBoxLayout(local_group)
        local_form = QFormLayout()
        self.model_profile_combo = QComboBox()
        self.model_profile_combo.addItem("자동 (설치 모델 우선)", "auto")
        self.model_profile_combo.addItem("속도 우선", "speed")
        self.model_profile_combo.addItem("균형", "balanced")
        self.model_profile_combo.addItem("품질 우선", "quality")
        self.model_profile_combo.addItem("직접 선택", "manual")
        self.hardware_scan_button = QPushButton("사양 다시 검사")
        profile_row = QWidget()
        profile_layout = QHBoxLayout(profile_row)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.addWidget(self.model_profile_combo, 1)
        profile_layout.addWidget(self.hardware_scan_button)
        self.hardware_status = QLabel("사양 검사를 아직 실행하지 않았습니다.")
        self.hardware_status.setWordWrap(True)
        self.recommendation_status = QLabel("추천 모델 없음")
        self.recommendation_status.setWordWrap(True)
        self.use_recommendation_button = QPushButton("추천 모델 선택 (다운로드 안 함)")
        self.manage_models_button = QPushButton("Ollama 모델 관리…")
        self.use_recommendation_button.setEnabled(False)
        local_form.addRow("추천 프로필", profile_row)
        local_form.addRow("PC / Ollama", self.hardware_status)
        local_form.addRow("추천", self.recommendation_status)
        local_form.addRow("선택", self.use_recommendation_button)
        local_form.addRow("설치/삭제", self.manage_models_button)
        local_layout.addLayout(local_form)
        self.model_candidates = QPlainTextEdit()
        self.model_candidates.setReadOnly(True)
        self.model_candidates.setMaximumHeight(145)
        self.model_candidates.setPlaceholderText(
            "검사 후 모델별 다운로드 크기, 예상 실행 메모리와 적합도를 표시합니다."
        )
        local_layout.addWidget(self.model_candidates)
        local_note = QLabel(
            "추천 선택은 모델명만 저장하며 다운로드를 시작하지 않습니다. "
            "모델 다운로드·삭제는 관리 화면에서 용량을 확인하고 명시적으로 실행합니다."
        )
        local_note.setWordWrap(True)
        local_note.setStyleSheet("color: #666;")
        local_layout.addWidget(local_note)
        root.addWidget(local_group)

        note = QLabel(
            "API 키는 설정 JSON에 저장하지 않고 Windows 자격 증명 저장소에 "
            "보관합니다. 클라우드 전송 전에는 즉시 요약 화면에서 실제 페이지와 "
            "예상 토큰을 다시 확인할 수 있습니다."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._save_preferences)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.key_save_button.clicked.connect(self._save_key)
        self.key_delete_button.clicked.connect(self._delete_key)
        self.hardware_scan_button.clicked.connect(self._scan_hardware)
        self.use_recommendation_button.clicked.connect(self._use_recommendation)
        self.manage_models_button.clicked.connect(self._open_model_manager)
        self._load()

    def _load(self) -> None:
        view = self._controller.view()
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        selected_index = 0
        for index, choice in enumerate(view.provider_choices):
            self.provider_combo.addItem(choice.label, choice.provider)
            if choice.provider == view.provider:
                selected_index = index
        self.provider_combo.setCurrentIndex(selected_index)
        self.provider_combo.blockSignals(False)
        self.model_edit.setText(view.model)
        self.consent_check.setChecked(view.cloud_processing_consent)
        profile_index = self.profile_combo.findData(view.cloud_request_profile)
        self.profile_combo.setCurrentIndex(max(0, profile_index))
        settings = self._controller.settings()
        self.parallel_spin.setValue(settings.cloud_max_parallel_requests)
        self.budget_spin.setValue(settings.cloud_monthly_budget_usd)
        model_profile_index = self.model_profile_combo.findData(view.model_profile)
        self.model_profile_combo.setCurrentIndex(max(0, model_profile_index))
        if view.last_hardware_scan_at:
            self.hardware_status.setText(
                f"마지막 검사: {view.last_hardware_scan_at}"
            )
        if view.recommended_model:
            self._recommended_model = view.recommended_model
            profile_note = (
                f"{view.recommendation_profile} 프로필 · "
                if view.recommendation_profile
                else ""
            )
            self.recommendation_status.setText(
                f"저장된 추천: {view.recommended_model}"
                f" ({profile_note}사양 재검사 권장)"
            )
            self.use_recommendation_button.setEnabled(True)
        self._refresh_key_status()
        self._profile_changed()

    def _provider_changed(self) -> None:
        provider = self.provider_combo.currentData()
        if provider:
            self.model_edit.setText(self._controller.model_for_provider(provider))
        self.key_edit.clear()
        self._refresh_key_status()
        self._profile_changed()

    def _refresh_key_status(self) -> None:
        provider = self.provider_combo.currentData()
        is_cloud = provider in {"openai", "anthropic"}
        status = self._controller.key_status(provider) if provider else None
        self.key_status.setText(
            f"등록됨 {status.masked_hint}" if status and status.configured else "등록 안 됨"
        )
        for widget in (
            self.key_edit,
            self.key_save_button,
            self.key_delete_button,
            self.consent_check,
            self.profile_combo,
            self.parallel_spin,
            self.budget_spin,
        ):
            widget.setEnabled(is_cloud)
        self.key_delete_button.setEnabled(bool(is_cloud and status and status.configured))

    def _profile_changed(self) -> None:
        profile = self.profile_combo.currentData()
        self.parallel_spin.setEnabled(
            self.provider_combo.currentData() in {"openai", "anthropic"}
            and profile == "high_throughput"
        )
        if profile == "conservative":
            self.parallel_spin.setValue(1)
        elif profile == "standard":
            self.parallel_spin.setValue(2)

    def _save_key(self) -> None:
        provider = self.provider_combo.currentData()
        value = self.key_edit.text()
        try:
            self._controller.save_api_key(provider, value)
        except Exception as exc:
            QMessageBox.warning(self, "API 키 저장 실패", str(exc))
            return
        self.key_edit.clear()
        self._refresh_key_status()

    def _delete_key(self) -> None:
        provider = self.provider_combo.currentData()
        if QMessageBox.question(
            self,
            "API 키 삭제",
            "이 PC의 Paper Organizer 자격 증명에서 키를 삭제할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            self._controller.delete_api_key(provider)
        except Exception as exc:
            QMessageBox.warning(self, "API 키 삭제 실패", str(exc))
            return
        self._refresh_key_status()

    def _scan_hardware(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        self.hardware_scan_button.setEnabled(False)
        self.hardware_status.setText("CPU·RAM·GPU·디스크와 Ollama를 검사하는 중…")
        self.recommendation_status.setText("추천 계산 중…")
        self.use_recommendation_button.setEnabled(False)
        worker = _HardwareScanWorker(
            self._controller,
            self.model_profile_combo.currentData(),
            self,
        )
        worker.completed.connect(self._hardware_scan_completed)
        worker.failed.connect(self._hardware_scan_failed)
        worker.finished.connect(lambda: self.hardware_scan_button.setEnabled(True))
        self._scan_worker = worker
        worker.start()

    def _hardware_scan_completed(self, assessment) -> None:
        hardware = assessment.hardware
        gpu_text = ", ".join(
            f"{gpu.name} ({gpu.vram_total_gb:g}GB)"
            if gpu.vram_total_gb is not None
            else gpu.name
            for gpu in hardware.gpus
        ) or "GPU 미감지 · CPU 실행"
        ollama = assessment.ollama
        ollama_text = (
            f"Ollama {ollama.version}, 설치 모델 {len(ollama.models)}개"
            if ollama.reachable
            else "Ollama 연결 안 됨"
        )
        self.hardware_status.setText(
            f"{hardware.cpu_model} · 코어 {hardware.logical_cores} · "
            f"RAM {hardware.memory_available_gb:g}/{hardware.memory_total_gb:g}GB 사용 가능 · "
            f"{gpu_text} · 모델 디스크 {hardware.model_disk_free_gb:g}GB 여유 · {ollama_text}"
        )
        recommendation = assessment.recommendation
        chosen = recommendation.recommended
        if chosen is None:
            self._recommended_model = ""
            self.recommendation_status.setText(
                "현재 안전 여유 기준으로 추천할 로컬 모델이 없습니다."
            )
            self.use_recommendation_button.setEnabled(False)
        else:
            self._recommended_model = chosen.spec.model_id
            state = "설치됨" if chosen.installed else f"다운로드 약 {chosen.spec.download_gb:g}GB"
            explanation = " ".join((*chosen.reasons, *chosen.warnings))
            self.recommendation_status.setText(
                f"{chosen.spec.label} ({chosen.rating}, {state}) · "
                + explanation
            )
            self.use_recommendation_button.setEnabled(True)
        lines: list[str] = []
        for candidate in recommendation.candidates:
            installed = " · 설치됨" if candidate.installed else ""
            warning = f" · {' '.join(candidate.warnings)}" if candidate.warnings else ""
            lines.append(
                f"[{candidate.rating}] {candidate.spec.label} — 다운로드 "
                f"{candidate.spec.download_gb:g}GB / 예상 실행 메모리 "
                f"{candidate.spec.runtime_memory_gb:g}GB{installed}{warning}"
            )
        self.model_candidates.setPlainText("\n".join(lines))

    def _hardware_scan_failed(self, message: str) -> None:
        self.hardware_status.setText(f"사양 검사 실패: {message}")
        self.recommendation_status.setText("추천을 계산하지 못했습니다.")
        self.use_recommendation_button.setEnabled(False)

    def _use_recommendation(self) -> None:
        if not self._recommended_model:
            return
        ollama_index = self.provider_combo.findData("ollama")
        if ollama_index >= 0:
            self.provider_combo.setCurrentIndex(ollama_index)
        self.model_edit.setText(self._recommended_model)

    def _open_model_manager(self) -> None:
        dialog = OllamaModelDialog(self._controller, self)
        dialog.model_verified.connect(self._model_install_verified)
        dialog.model_deleted.connect(self._model_deleted)
        dialog.refresh()
        dialog.exec_()

    def _model_install_verified(self, model: str) -> None:
        ollama_index = self.provider_combo.findData("ollama")
        if ollama_index >= 0:
            self.provider_combo.setCurrentIndex(ollama_index)
        self.model_edit.setText(model)

    def _model_deleted(self, model: str, selection_cleared: bool) -> None:
        if selection_cleared or self.model_edit.text().strip().casefold() == model.casefold():
            self.model_edit.clear()

    def _save_preferences(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            QMessageBox.information(
                self,
                "사양 검사 중",
                "사양 검사가 끝난 뒤 설정을 저장하세요.",
            )
            return
        try:
            self._controller.save_preferences(
                provider=self.provider_combo.currentData(),
                model=self.model_edit.text(),
                cloud_processing_consent=self.consent_check.isChecked(),
                cloud_request_profile=self.profile_combo.currentData(),
                cloud_max_parallel_requests=self.parallel_spin.value(),
                cloud_monthly_budget_usd=self.budget_spin.value(),
                model_profile=self.model_profile_combo.currentData(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "AI 설정 저장 실패", str(exc))
            return
        self.accept()

    def reject(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            QMessageBox.information(
                self,
                "사양 검사 중",
                "사양 검사가 끝난 뒤 창을 닫으세요.",
            )
            return
        super().reject()
