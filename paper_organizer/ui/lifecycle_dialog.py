"""Mandatory first-run choices for login startup and close behavior."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
)

from paper_organizer.application.lifecycle import LifecycleSettingsController
from paper_organizer.ui.dialog_utils import suppress_context_help_button


class LifecyclePreferencesDialog(QDialog):
    def __init__(
        self,
        controller: LifecycleSettingsController,
        *,
        first_run: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        suppress_context_help_button(self)
        self._controller = controller
        self.setWindowTitle(
            "Paper Organizer 첫 실행 설정" if first_run else "시작 및 종료 설정"
        )
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)

        title = QLabel(
            "처음 사용할 때 시작 방식과 X 버튼 동작을 선택해 주세요."
            if first_run
            else "Windows 로그인과 창 닫기 동작을 변경합니다."
        )
        title.setWordWrap(True)
        root.addWidget(title)

        self.start_with_windows_check = QCheckBox(
            "Windows 로그인과 동시에 Paper Organizer 실행"
        )
        self.start_with_windows_check.setToolTip(
            "현재 사용자 계정에만 적용되며 관리자 권한이 필요하지 않습니다."
        )
        root.addWidget(self.start_with_windows_check)

        close_group = QGroupBox("X 버튼을 눌렀을 때")
        close_layout = QVBoxLayout(close_group)
        self.background_radio = QRadioButton(
            "백그라운드에서 계속 실행 (시스템 트레이에서 다시 열기)"
        )
        self.quit_radio = QRadioButton("Paper Organizer 완전 종료")
        self.close_button_group = QButtonGroup(self)
        self.close_button_group.addButton(self.background_radio)
        self.close_button_group.addButton(self.quit_radio)
        close_layout.addWidget(self.background_radio)
        close_layout.addWidget(self.quit_radio)
        root.addWidget(close_group)

        note = QLabel(
            "백그라운드 실행을 선택하면 켜 둔 다운로드 폴더 감시와 분석 큐가 유지됩니다. "
            "작업 중인 분석이 있을 때는 어느 동작을 선택해도 즉시 종료하지 않습니다."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.save_button = self.buttons.button(QDialogButtonBox.Save)
        self.save_button.setText("선택 저장")
        self.buttons.button(QDialogButtonBox.Cancel).setText(
            "앱 종료" if first_run else "취소"
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        settings = controller.settings()
        self.start_with_windows_check.setChecked(settings.start_with_windows)
        if not first_run:
            if settings.close_behavior == "background":
                self.background_radio.setChecked(True)
            else:
                self.quit_radio.setChecked(True)
        self.save_button.setEnabled(not first_run)
        self.background_radio.toggled.connect(self._update_save_enabled)
        self.quit_radio.toggled.connect(self._update_save_enabled)

    def _update_save_enabled(self) -> None:
        self.save_button.setEnabled(
            self.background_radio.isChecked() or self.quit_radio.isChecked()
        )

    def _save(self) -> None:
        if self.background_radio.isChecked():
            close_behavior = "background"
        elif self.quit_radio.isChecked():
            close_behavior = "quit"
        else:
            QMessageBox.information(self, "선택 필요", "X 버튼 동작을 선택하세요.")
            return
        try:
            self._controller.save_preferences(
                start_with_windows=self.start_with_windows_check.isChecked(),
                close_behavior=close_behavior,
            )
        except Exception as exc:
            QMessageBox.warning(self, "설정 저장 실패", str(exc))
            return
        self.accept()
