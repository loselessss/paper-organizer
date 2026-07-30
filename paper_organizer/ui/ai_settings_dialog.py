"""PyQt dialog for provider, model, consent, throughput and credential controls."""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.ai_settings import AiSettingsController
from paper_organizer.core.model_recommendation import model_usage_guidance
from paper_organizer.core.ollama_residency import (
    OLLAMA_RESIDENCY_CHOICES,
    residency_description,
)
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


class _OllamaRestartWorker(QThread):
    completed = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, controller: AiSettingsController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller

    def run(self) -> None:
        try:
            if not self._controller.restart_ollama_runtime():
                raise RuntimeError(
                    "Ollama를 다시 시작하지 못했습니다. 시작 메뉴에서 직접 실행하세요."
                )
            self.completed.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class AiSettingsDialog(QDialog):
    def __init__(self, controller: AiSettingsController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("요약 엔진 옵션 · 논문 프롬프트 v9 · 특허 v1")
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        available_width = available.width() if available is not None else 1160
        available_height = available.height() if available is not None else 760
        self.resize(
            max(360, min(1120, available_width - 40)),
            max(320, min(720, available_height - 40)),
        )
        self.setMinimumSize(
            max(320, min(620, available_width - 80)),
            max(280, min(480, available_height - 80)),
        )
        self._scan_worker: _HardwareScanWorker | None = None
        self._restart_worker: _OllamaRestartWorker | None = None
        self._recommended_model = ""
        self._initial_force_igpu = False

        root = QVBoxLayout(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        engine_group = QGroupBox("요약 엔진 옵션")
        engine_layout = QVBoxLayout(engine_group)

        self.engine_columns = QBoxLayout(QBoxLayout.LeftToRight)
        self.provider_group = QGroupBox("제공자·출력")
        form = QFormLayout(self.provider_group)
        self.provider_combo = QComboBox()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_refresh_button = QPushButton("새로고침")
        self.model_status = QLabel("")
        self.model_status.setWordWrap(True)
        self.model_status.setStyleSheet("color: #666;")
        self.model_guidance = QLabel("")
        self.model_guidance.setWordWrap(True)
        self.model_guidance.setStyleSheet(
            "background: #fff8e8; border: 1px solid #e2c98d; "
            "border-radius: 4px; padding: 7px; color: #5f4200;"
        )
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
        self.language_combo = QComboBox()
        self.language_combo.addItem("한국어로 번역", "ko")
        self.language_combo.addItem("논문 원문 언어 유지", "source")
        self.language_combo.setToolTip(
            "제목·저자·저널 같은 서지정보는 원문 표기를 유지하고, "
            "요약·핵심 결과 같은 설명 필드의 언어를 선택합니다."
        )
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(60, 3600)
        self.timeout_spin.setSuffix("초")
        self.timeout_spin.setSingleStep(60)
        self.timeout_spin.setToolTip(
            "AI 요청 한 번의 최대 대기 시간입니다. 느린 로컬 모델에는 900초 이상을 권장합니다."
        )
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
        form.addRow("키 상태", self.key_status)
        form.addRow("API 키", key_buttons)
        form.addRow("클라우드 동의", self.consent_check)
        form.addRow("요약 언어", self.language_combo)
        form.addRow("요약 제한 시간", self.timeout_spin)
        form.addRow("클라우드 처리량", self.profile_combo)
        form.addRow("최대 병렬 요청", self.parallel_spin)
        form.addRow("월간 앱 비용 한도", self.budget_spin)
        self.engine_columns.addWidget(self.provider_group, 1)

        self.local_model_group = QGroupBox("모델 선택·Ollama 설치 및 삭제")
        local_layout = QVBoxLayout(self.local_model_group)
        local_form = QFormLayout()
        self.manage_models_button = QPushButton("Ollama 설치·삭제…")
        model_row = QWidget()
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(self.model_combo, 1)
        model_layout.addWidget(self.model_refresh_button)
        model_layout.addWidget(self.manage_models_button)
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
        self.residency_combo = QComboBox()
        for value, label in OLLAMA_RESIDENCY_CHOICES:
            self.residency_combo.addItem(label, value)
        self.resident_model_combo = QComboBox()
        self.force_igpu_check = QCheckBox(
            "내장 GPU도 사용 허용 (Vulkan · 사용할 수 없으면 CPU)"
        )
        self.force_igpu_check.setToolTip(
            "Ollama가 Intel Iris Xe 같은 내장 GPU를 후보에서 제외하지 않도록 합니다. "
            "설정 변경 후 Ollama를 완전히 종료하고 다시 실행해야 합니다."
        )
        self.igpu_guidance = QLabel(
            "외장 GPU는 Ollama가 자동으로 우선 사용합니다. 이 옵션은 내장 GPU도 "
            "후보에 포함하지만 GPU 사용을 보장하지 않으며, 드라이버·메모리 조건이 "
            "맞지 않으면 CPU로 실행됩니다. 내장 GPU는 1.7B 모델부터 시험하세요."
        )
        self.igpu_guidance.setWordWrap(True)
        self.igpu_guidance.setStyleSheet("color: #666;")
        self.residency_guidance = QLabel("")
        self.residency_guidance.setWordWrap(True)
        self.residency_guidance.setStyleSheet(
            "background: #eef6ff; border: 1px solid #aec9e8; "
            "border-radius: 4px; padding: 7px; color: #173f68;"
        )
        local_form.addRow("활성 모델", model_row)
        local_form.addRow("", self.model_status)
        local_form.addRow("용도 / 주의", self.model_guidance)
        local_form.addRow("상주 옵션", self.residency_combo)
        local_form.addRow("상주 모델", self.resident_model_combo)
        local_form.addRow("상주 설명", self.residency_guidance)
        local_form.addRow("GPU 가속", self.force_igpu_check)
        local_form.addRow("", self.igpu_guidance)
        local_form.addRow("추천 프로필", profile_row)
        local_form.addRow("PC / Ollama", self.hardware_status)
        local_form.addRow("추천", self.recommendation_status)
        local_layout.addLayout(local_form)
        self.model_candidates = QPlainTextEdit()
        self.model_candidates.setReadOnly(True)
        self.model_candidates.setMaximumHeight(145)
        self.model_candidates.setPlaceholderText(
            "검사 후 모델별 다운로드 크기, 예상 실행 메모리와 적합도를 표시합니다."
        )
        local_layout.addWidget(self.model_candidates)
        local_note = QLabel(
            "설치된 모델을 고르면 즉시 활성 모델로 저장됩니다. 옆의 설치·삭제에서 "
            "다운로드와 제거를 한 화면에서 관리하며, 파일 변경은 사용자 승인 후 실행합니다."
        )
        local_note.setWordWrap(True)
        local_note.setStyleSheet("color: #666;")
        local_layout.addWidget(local_note)
        self.engine_columns.addWidget(self.local_model_group, 1)
        engine_layout.addLayout(self.engine_columns)
        scroll_layout.addWidget(engine_group)

        note = QLabel(
            "API 키는 일반 설정 파일에 저장하지 않고 Windows 자격 증명 저장소에 "
            "보관합니다. 클라우드 분석은 위의 지속 전송 동의를 켠 경우에만 실행됩니다."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        scroll_layout.addWidget(note)
        scroll_layout.addStretch(1)
        self.scroll_area.setWidget(scroll_content)
        root.addWidget(self.scroll_area, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.buttons.accepted.connect(self._save_preferences)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        self.residency_combo.currentIndexChanged.connect(
            self._update_residency_guidance
        )
        self.resident_model_combo.currentIndexChanged.connect(
            self._update_residency_guidance
        )
        self.model_refresh_button.clicked.connect(self._reload_ollama_models)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.key_save_button.clicked.connect(self._save_key)
        self.key_delete_button.clicked.connect(self._delete_key)
        self.hardware_scan_button.clicked.connect(self._scan_hardware)
        self.manage_models_button.clicked.connect(
            lambda: self._open_model_manager()
        )
        self._load()
        self.model_profile_combo.currentIndexChanged.connect(
            self._model_profile_changed
        )
        self._update_responsive_layout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_responsive_layout()

    def _update_responsive_layout(self) -> None:
        columns = getattr(self, "engine_columns", None)
        if columns is None:
            return
        direction = (
            QBoxLayout.TopToBottom
            if self.width() < 1040
            else QBoxLayout.LeftToRight
        )
        if columns.direction() != direction:
            columns.setDirection(direction)

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
        self._populate_model_combo(view.provider, view.model)
        if view.provider != "ollama":
            self._populate_resident_model_combo(
                (),
                view.ollama_resident_model
                or self._controller.model_for_provider("ollama"),
            )
        residency_index = self.residency_combo.findData(
            view.ollama_residency_mode
        )
        self.residency_combo.setCurrentIndex(max(0, residency_index))
        self.force_igpu_check.setChecked(view.ollama_force_igpu)
        self._initial_force_igpu = view.ollama_force_igpu
        self.consent_check.setChecked(view.cloud_processing_consent)
        language_index = self.language_combo.findData(view.summary_language)
        self.language_combo.setCurrentIndex(max(0, language_index))
        self.timeout_spin.setValue(view.summary_timeout_seconds)
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
        self._refresh_key_status()
        self._profile_changed()
        self._update_residency_guidance()

    def _provider_changed(self) -> None:
        provider = self.provider_combo.currentData()
        if provider:
            self._populate_model_combo(
                provider,
                self._controller.model_for_provider(provider),
            )
        self.key_edit.clear()
        self._refresh_key_status()
        self._profile_changed()

    def _populate_model_combo(self, provider: str, selected: str) -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        is_ollama = provider == "ollama"
        self.model_combo.setEditable(not is_ollama)
        self.model_refresh_button.setVisible(is_ollama)
        if is_ollama:
            try:
                models = self._controller.installed_ollama_models()
            except Exception as exc:
                models = ()
                self.model_status.setText(f"설치 모델을 불러오지 못했습니다: {exc}")
            else:
                self.model_status.setText(
                    f"설치된 모델 {len(models)}개 · 선택하면 즉시 적용됩니다."
                    if models
                    else "설치된 모델이 없습니다. 아래 모델 관리에서 먼저 설치하세요."
                )
            for model in models:
                self.model_combo.addItem(model, model)
            index = self.model_combo.findData(selected)
            self.model_combo.setCurrentIndex(index)
            self.model_combo.setEnabled(bool(models))
            self._populate_resident_model_combo(
                models,
                self._controller.view().ollama_resident_model or selected,
            )
        else:
            self.model_combo.setEnabled(True)
            self.model_combo.addItem(selected)
            self.model_combo.setCurrentText(selected)
            self.model_status.setText("클라우드 모델 ID는 저장 버튼을 누를 때 적용됩니다.")
            line_edit = self.model_combo.lineEdit()
            if line_edit is not None:
                line_edit.setPlaceholderText("클라우드 모델 ID")
        self.model_combo.blockSignals(False)
        self._update_model_guidance()

    def _populate_resident_model_combo(
        self,
        models: tuple[str, ...],
        selected: str,
    ) -> None:
        self.resident_model_combo.blockSignals(True)
        self.resident_model_combo.clear()
        choices = list(models)
        if selected and not any(
            _same_ollama_model(selected, model) for model in choices
        ):
            choices.append(selected)
        for model in choices:
            self.resident_model_combo.addItem(model, model)
        index = next(
            (
                item
                for item in range(self.resident_model_combo.count())
                if _same_ollama_model(
                    str(self.resident_model_combo.itemData(item) or ""),
                    selected,
                )
            ),
            -1,
        )
        if index < 0 and self.resident_model_combo.count():
            index = 0
        self.resident_model_combo.setCurrentIndex(index)
        self.resident_model_combo.setEnabled(bool(choices))
        self.resident_model_combo.blockSignals(False)
        self._update_residency_guidance()

    def _reload_ollama_models(self) -> None:
        if self.provider_combo.currentData() != "ollama":
            return
        self._populate_model_combo(
            "ollama",
            self._controller.model_for_provider("ollama"),
        )

    def _model_changed(self) -> None:
        self._update_model_guidance()
        if self.provider_combo.currentData() != "ollama":
            return
        model = self.model_combo.currentData()
        if not model:
            return
        try:
            self._controller.select_ollama_model(str(model))
        except Exception as exc:
            QMessageBox.warning(self, "Ollama 모델 적용 실패", str(exc))
            return
        self.model_status.setText(f"{model} 적용 완료")

    def _update_model_guidance(self) -> None:
        provider = self.provider_combo.currentData()
        if provider == "ollama":
            model = str(self.model_combo.currentData() or "")
            if not model:
                self.model_guidance.setText(
                    "설치된 모델을 선택하면 실사용 용도와 환각 주의사항을 표시합니다."
                )
                return
            self.model_guidance.setText(
                model_usage_guidance(model).display_text()
            )
            return
        if provider in {"openai", "anthropic"}:
            self.model_guidance.setText(
                "클라우드 정밀 분석 · 모델별 품질과 비용은 제공자 정책에 따라 달라집니다.\n"
                "논문 본문이 외부 서비스로 전송되며 API 키와 제공자 지출 한도를 "
                "별도로 관리해야 합니다."
            )
            return
        self.model_guidance.clear()

    def _update_residency_guidance(self) -> None:
        settings = self._controller.settings()
        memory = settings.hardware_profile.get("memory_total_gb")
        if not isinstance(memory, (int, float)):
            memory = None
        self.residency_guidance.setText(
            residency_description(
                str(self.residency_combo.currentData() or "auto"),
                str(self.resident_model_combo.currentData() or ""),
                memory,
            )
        )

    def _current_model(self) -> str:
        if self.provider_combo.currentData() == "ollama":
            return str(self.model_combo.currentData() or "")
        return self.model_combo.currentText().strip()

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
        self.model_profile_combo.setEnabled(False)
        self.hardware_status.setText("CPU·RAM·GPU·디스크와 Ollama를 검사하는 중…")
        self.recommendation_status.setText(
            f"{self.model_profile_combo.currentText()} 프로필로 추천 계산 중…"
        )
        worker = _HardwareScanWorker(
            self._controller,
            self.model_profile_combo.currentData(),
            self,
        )
        worker.completed.connect(self._hardware_scan_completed)
        worker.failed.connect(self._hardware_scan_failed)
        worker.finished.connect(self._hardware_scan_finished)
        self._scan_worker = worker
        worker.start()

    def _model_profile_changed(self) -> None:
        self._scan_hardware()

    def _hardware_scan_finished(self) -> None:
        self.hardware_scan_button.setEnabled(True)
        self.model_profile_combo.setEnabled(True)

    def _hardware_scan_completed(self, assessment) -> None:
        hardware = assessment.hardware
        gpu_text = ", ".join(
            f"{gpu.name} ({gpu.vram_total_gb:g}GB)"
            if gpu.vram_total_gb is not None
            else gpu.name
            for gpu in hardware.gpus
        ) or "GPU 미감지 · CPU 실행"
        ollama = assessment.ollama
        running_text = ", ".join(
            f"{model.name} {model.processor}"
            for model in ollama.running_models
        ) or "현재 적재 모델 없음"
        ollama_text = (
            f"Ollama {ollama.version}, 설치 모델 {len(ollama.models)}개, "
            f"{running_text}"
            if ollama.reachable
            else "Ollama 연결 안 됨"
        )
        self.hardware_status.setText(
            f"{hardware.cpu_model} · 코어 {hardware.logical_cores} · "
            f"RAM {hardware.memory_available_gb:g}/{hardware.memory_total_gb:g}GB 사용 가능 · "
            f"{gpu_text} · 모델 디스크 {hardware.model_disk_free_gb:g}GB 여유 · {ollama_text}"
        )
        recommendation = assessment.recommendation
        profile_label = {
            "auto": "자동",
            "speed": "속도 우선",
            "balanced": "균형",
            "quality": "품질 우선",
            "manual": "직접 선택",
        }.get(recommendation.profile, recommendation.profile)
        chosen = recommendation.recommended
        if chosen is None:
            self._recommended_model = ""
            self.recommendation_status.setText(
                f"{profile_label} 결과 · 현재 안전 여유 기준으로 추천할 "
                "로컬 모델이 없습니다."
            )
        else:
            self._recommended_model = chosen.spec.model_id
            state = "설치됨" if chosen.installed else f"다운로드 약 {chosen.spec.download_gb:g}GB"
            explanation = " ".join((*chosen.reasons, *chosen.warnings))
            self.recommendation_status.setText(
                f"{profile_label} 결과 · {chosen.spec.label} "
                f"({chosen.rating}, {state}) · "
                + explanation
                + " 추천 결과만 바뀌며 활성 모델은 자동 변경하지 않습니다."
            )
        lines: list[str] = []
        for candidate in recommendation.candidates:
            marker = (
                "★ 프로필 추천 · "
                if chosen is not None
                and candidate.spec.model_id == chosen.spec.model_id
                else ""
            )
            installed = " · 설치됨" if candidate.installed else ""
            warning = f" · {' '.join(candidate.warnings)}" if candidate.warnings else ""
            usage = model_usage_guidance(
                candidate.spec.model_id,
                candidate.spec.parameters_b,
            )
            lines.append(
                f"{marker}[{candidate.rating}] {candidate.spec.label} — 다운로드 "
                f"{candidate.spec.download_gb:g}GB / 예상 실행 메모리 "
                f"{candidate.spec.runtime_memory_gb:g}GB · {usage.role}, "
                f"환각 위험 {usage.hallucination_risk}{installed}{warning}"
            )
        self.model_candidates.setPlainText("\n".join(lines))
        self._update_residency_guidance()

    def _hardware_scan_failed(self, message: str) -> None:
        self.hardware_status.setText(f"사양 검사 실패: {message}")
        self.recommendation_status.setText("추천을 계산하지 못했습니다.")

    def _open_model_manager(self, initial_model: str = "") -> None:
        preferred = (
            initial_model
            or self._recommended_model
            or self._controller.model_for_provider("ollama")
        )
        dialog = OllamaModelDialog(
            self._controller,
            self,
            initial_model=preferred,
        )
        dialog.model_verified.connect(self._model_install_verified)
        dialog.model_deleted.connect(self._model_deleted)
        dialog.refresh()
        dialog.exec_()

    def _model_install_verified(self, model: str) -> None:
        ollama_index = self.provider_combo.findData("ollama")
        if ollama_index >= 0:
            self.provider_combo.setCurrentIndex(ollama_index)
        self._populate_model_combo("ollama", model)

    def _model_deleted(self, model: str, selection_cleared: bool) -> None:
        if self.provider_combo.currentData() == "ollama":
            selected = "" if selection_cleared else self._current_model()
            self._populate_model_combo("ollama", selected)
            return
        try:
            models = self._controller.installed_ollama_models()
        except Exception:
            models = ()
        view = self._controller.view()
        self._populate_resident_model_combo(
            models,
            view.ollama_resident_model,
        )

    def _save_preferences(self) -> None:
        if self._scan_worker is not None and self._scan_worker.isRunning():
            QMessageBox.information(
                self,
                "사양 검사 중",
                "사양 검사가 끝난 뒤 설정을 저장하세요.",
            )
            return
        acceleration_changed = (
            self.force_igpu_check.isChecked()
            != self._initial_force_igpu
        )
        if acceleration_changed and QMessageBox.question(
            self,
            "Ollama GPU 설정 변경",
            "GPU 설정을 저장하면 Ollama를 바로 다시 시작합니다.\n\n"
            "현재 진행 중인 다른 앱의 Ollama 작업도 중단됩니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Yes,
        ) != QMessageBox.Yes:
            return
        try:
            self._controller.save_preferences(
                provider=self.provider_combo.currentData(),
                model=self._current_model(),
                cloud_processing_consent=self.consent_check.isChecked(),
                cloud_request_profile=self.profile_combo.currentData(),
                cloud_max_parallel_requests=self.parallel_spin.value(),
                cloud_monthly_budget_usd=self.budget_spin.value(),
                model_profile=self.model_profile_combo.currentData(),
                summary_language=self.language_combo.currentData(),
                summary_timeout_seconds=self.timeout_spin.value(),
                ollama_residency_mode=self.residency_combo.currentData(),
                ollama_resident_model=str(
                    self.resident_model_combo.currentData() or ""
                ),
                ollama_force_igpu=self.force_igpu_check.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "요약 엔진 설정 저장 실패", str(exc))
            return
        if acceleration_changed:
            state = "사용" if self.force_igpu_check.isChecked() else "사용하지 않도록"
            self.scroll_area.setEnabled(False)
            self.buttons.setEnabled(False)
            self.hardware_status.setText(
                f"내장 GPU를 {state} 설정했습니다. Ollama를 다시 시작하는 중…"
            )
            worker = _OllamaRestartWorker(self._controller, self)
            worker.completed.connect(self._restart_completed)
            worker.failed.connect(self._restart_failed)
            worker.finished.connect(worker.deleteLater)
            self._restart_worker = worker
            worker.start()
            return
        self.accept()

    def _restart_completed(self) -> None:
        self._restart_worker = None
        QMessageBox.information(
            self,
            "Ollama 재시작 완료",
            "GPU 설정을 저장하고 Ollama를 다시 시작했습니다.\n\n"
            "이 옵션은 내장 GPU를 후보에 포함하며 GPU 실행 자체를 강제하지는 "
            "않습니다. 모델 검증 또는 사양 다시 검사에서 실제 CPU·GPU 상태를 "
            "확인할 수 있습니다.",
        )
        self.accept()

    def _restart_failed(self, message: str) -> None:
        self._restart_worker = None
        self.scroll_area.setEnabled(True)
        self.buttons.setEnabled(True)
        self.hardware_status.setText("Ollama 자동 재시작에 실패했습니다.")
        QMessageBox.warning(self, "Ollama 재시작 실패", message)

    def reject(self) -> None:
        if self._restart_worker is not None and self._restart_worker.isRunning():
            QMessageBox.information(
                self,
                "Ollama 재시작 중",
                "Ollama 재시작이 끝난 뒤 창을 닫으세요.",
            )
            return
        if self._scan_worker is not None and self._scan_worker.isRunning():
            QMessageBox.information(
                self,
                "사양 검사 중",
                "사양 검사가 끝난 뒤 창을 닫으세요.",
            )
            return
        super().reject()


def _same_ollama_model(left: str, right: str) -> bool:
    return (
        left.strip().casefold().removesuffix(":latest")
        == right.strip().casefold().removesuffix(":latest")
    )
