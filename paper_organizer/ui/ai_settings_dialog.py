"""PyQt dialog for provider, model, consent, throughput and credential controls."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.ai_settings import AiSettingsController


class AiSettingsDialog(QDialog):
    def __init__(self, controller: AiSettingsController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("요약 AI 설정")
        self.resize(620, 410)

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

    def _save_preferences(self) -> None:
        try:
            self._controller.save_preferences(
                provider=self.provider_combo.currentData(),
                model=self.model_edit.text(),
                cloud_processing_consent=self.consent_check.isChecked(),
                cloud_request_profile=self.profile_combo.currentData(),
                cloud_max_parallel_requests=self.parallel_spin.value(),
                cloud_monthly_budget_usd=self.budget_spin.value(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "AI 설정 저장 실패", str(exc))
            return
        self.accept()
