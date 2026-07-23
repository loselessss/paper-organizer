# 입력 폴더·라이브러리·자동 감시 주기를 설정하는 다이얼로그 (수집 화면에서 분리)
"""Folder and low-power watch settings moved out of the collection tab."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.library_workflow import LibraryWorkflowController


class FolderSettingsDialog(QDialog):
    """Save paths and watch preferences through the workflow controller."""

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("폴더 및 감시 설정")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        form = QFormLayout()
        input_row, self.input_edit = self._path_row(self._browse_input)
        library_row, self.library_edit = self._path_row(self._browse_library)
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("저사양/절전", "eco")
        self.profile_combo.addItem("균형", "balanced")
        self.profile_combo.addItem("고성능", "performance")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 3600)
        self.interval_spin.setSuffix("초")
        self.interval_spin.setToolTip("5초에서 1시간 사이로 설정할 수 있습니다.")
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.auto_check = QCheckBox("설정한 주기로 가볍게 검색 (안정된 새 PDF만 1회 분석)")
        self.remove_source_check = QCheckBox(
            "paperpack 검증 완료 후 입력 폴더의 원본 PDF 삭제"
        )
        self.remove_source_check.setToolTip(
            "기본값은 원본 유지입니다. 삭제 실패 시 새 paperpack을 롤백합니다."
        )
        form.addRow("입력 폴더", input_row)
        form.addRow("PaperPack 라이브러리", library_row)
        form.addRow("시스템 부하", self.profile_combo)
        form.addRow("스캔 주기", self.interval_spin)
        form.addRow("자동 감시", self.auto_check)
        form.addRow("입력 PDF", self.remove_source_check)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._load_settings()

    def _path_row(self, browse_slot) -> tuple[QWidget, QLineEdit]:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(browse_slot)
        layout.addWidget(edit, 1)
        layout.addWidget(browse)
        return container, edit

    def _load_settings(self) -> None:
        input_dir, library_root = self._controller.configured_paths()
        settings = self._controller.settings()
        self.input_edit.setText(str(input_dir))
        self.library_edit.setText(str(library_root))
        profile_index = self.profile_combo.findData(settings.resource_profile)
        self.profile_combo.setCurrentIndex(max(0, profile_index))
        self.interval_spin.setValue(settings.scan_interval_seconds)
        self.auto_check.setChecked(settings.auto_enabled)
        self.remove_source_check.setChecked(settings.remove_source_after_import)

    def _browse_input(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "입력 폴더 선택", self.input_edit.text()
        )
        if path:
            self.input_edit.setText(path)

    def _browse_library(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "PaperPack 라이브러리 선택", self.library_edit.text()
        )
        if path:
            self.library_edit.setText(path)

    def _profile_changed(self) -> None:
        defaults = {"eco": 300, "balanced": 60, "performance": 15}
        profile = self.profile_combo.currentData()
        if profile in defaults:
            self.interval_spin.setValue(defaults[profile])

    def _save(self) -> None:
        try:
            self._controller.save_paths(
                Path(self.input_edit.text().strip()),
                Path(self.library_edit.text().strip()),
                auto_enabled=self.auto_check.isChecked(),
                resource_profile=self.profile_combo.currentData(),
                scan_interval_seconds=self.interval_spin.value(),
                remove_source_after_import=self.remove_source_check.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "폴더 설정 실패", str(exc))
            return
        self.accept()
