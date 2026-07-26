# 입력 폴더·라이브러리·자동 감시 주기를 설정하는 다이얼로그 (수집 화면에서 분리)
"""Folder and low-power watch settings moved out of the collection tab."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.core.classifier import TaxonomyError, taxonomy_category_names


class FolderSettingsDialog(QDialog):
    """Save paths and watch preferences through the workflow controller."""

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.setWindowTitle("폴더·감시·연구분야 설정")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        form = QFormLayout()
        library_row, self.library_edit = self._path_row(self._browse_library)

        watch_group = QGroupBox("논문·특허 감시 폴더")
        watch_layout = QVBoxLayout(watch_group)
        self.watch_list = QListWidget()
        self.watch_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        watch_layout.addWidget(self.watch_list)
        watch_actions = QHBoxLayout()
        add_watch_button = QPushButton("폴더 추가…")
        remove_watch_button = QPushButton("선택 제거")
        add_watch_button.clicked.connect(self._add_watch_folder)
        remove_watch_button.clicked.connect(self._remove_watch_folders)
        watch_actions.addWidget(add_watch_button)
        watch_actions.addWidget(remove_watch_button)
        watch_actions.addStretch(1)
        watch_layout.addLayout(watch_actions)
        root.addWidget(watch_group)
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
        self.auto_organize_check = QCheckBox(
            "학술 논문으로 판정되고 중복이 없으면 승인 없이 자동 보관"
        )
        self.auto_organize_check.setToolTip(
            "중복 후보가 있거나 판정이 불확실한 PDF는 자동 보관하지 않고 "
            "수집 화면에 남겨 사람이 검토합니다."
        )
        form.addRow("PaperPack 라이브러리", library_row)
        form.addRow("시스템 부하", self.profile_combo)
        form.addRow("스캔 주기", self.interval_spin)
        form.addRow("자동 감시", self.auto_check)
        form.addRow("자동 보관", self.auto_organize_check)
        form.addRow("입력 PDF", self.remove_source_check)
        root.addLayout(form)

        focus_group = QGroupBox("연구분야 관리")
        focus_layout = QVBoxLayout(focus_group)
        focus_note = QLabel(
            "분야를 추가·수정·삭제할 수 있습니다. 체크한 분야가 있으면 그 "
            "분야로만 자동 분류하고, 아무것도 체크하지 않으면 목록 전체를 사용합니다."
        )
        focus_note.setWordWrap(True)
        focus_layout.addWidget(focus_note)
        self.focus_list = QListWidget()
        self.focus_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.focus_list.setMaximumHeight(180)
        focus_layout.addWidget(self.focus_list)
        focus_actions = QHBoxLayout()
        self.add_category_button = QPushButton("분야 추가")
        self.edit_category_button = QPushButton("이름 수정")
        self.remove_category_button = QPushButton("선택 삭제")
        self.add_category_button.clicked.connect(self._add_focus_category)
        self.edit_category_button.clicked.connect(self._edit_focus_category)
        self.remove_category_button.clicked.connect(self._remove_focus_categories)
        focus_actions.addWidget(self.add_category_button)
        focus_actions.addWidget(self.edit_category_button)
        focus_actions.addWidget(self.remove_category_button)
        focus_actions.addStretch(1)
        focus_layout.addLayout(focus_actions)
        self.focus_list.itemDoubleClicked.connect(
            lambda _item: self._edit_focus_category()
        )
        root.addWidget(focus_group)

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
        _input_dir, library_root = self._controller.configured_paths()
        settings = self._controller.settings()
        self.watch_list.clear()
        for folder in self._controller.configured_input_dirs():
            self.watch_list.addItem(str(folder))
        self.library_edit.setText(str(library_root))
        profile_index = self.profile_combo.findData(settings.resource_profile)
        self.profile_combo.setCurrentIndex(max(0, profile_index))
        self.interval_spin.setValue(settings.scan_interval_seconds)
        self.auto_check.setChecked(settings.auto_enabled)
        self.remove_source_check.setChecked(settings.remove_source_after_import)
        self.auto_organize_check.setChecked(settings.auto_organize_academic)
        self._load_focus_categories(
            settings.focus_categories,
            settings.research_categories,
        )

    def _load_focus_categories(
        self, selected: list[str], configured: list[str]
    ) -> None:
        names = list(configured)
        if not names:
            try:
                names = taxonomy_category_names()
            except TaxonomyError:
                names = []
        chosen = {name.strip() for name in selected}
        self.focus_list.clear()
        for name in names:
            entry = QListWidgetItem(name)
            entry.setFlags(entry.flags() | Qt.ItemIsUserCheckable)
            entry.setCheckState(Qt.Checked if name in chosen else Qt.Unchecked)
            self.focus_list.addItem(entry)

    def _checked_focus_categories(self) -> list[str]:
        return [
            self.focus_list.item(row).text()
            for row in range(self.focus_list.count())
            if self.focus_list.item(row).checkState() == Qt.Checked
        ]

    def _research_categories(self) -> list[str]:
        return [
            self.focus_list.item(row).text().strip()
            for row in range(self.focus_list.count())
        ]

    def _category_name(self, title: str, value: str = "") -> str | None:
        name, accepted = QInputDialog.getText(
            self,
            title,
            "연구분야 이름",
            text=value,
        )
        if not accepted:
            return None
        normalized = " ".join(name.split())
        if not normalized:
            QMessageBox.warning(self, "연구분야 이름 필요", "이름을 입력하세요.")
            return None
        if len(normalized) > 80 or "," in normalized:
            QMessageBox.warning(
                self,
                "연구분야 이름 확인",
                "이름은 80자 이하로 입력하고 쉼표는 사용하지 마세요.",
            )
            return None
        return normalized

    def _add_focus_category(self) -> None:
        name = self._category_name("연구분야 추가")
        if name is None:
            return
        if name.casefold() in {
            value.casefold() for value in self._research_categories()
        }:
            QMessageBox.warning(self, "중복 연구분야", "이미 같은 분야가 있습니다.")
            return
        entry = QListWidgetItem(name)
        entry.setFlags(entry.flags() | Qt.ItemIsUserCheckable)
        entry.setCheckState(Qt.Checked)
        self.focus_list.addItem(entry)
        self.focus_list.setCurrentItem(entry)

    def _edit_focus_category(self) -> None:
        selected = self.focus_list.selectedItems()
        if len(selected) != 1:
            QMessageBox.information(
                self, "연구분야 선택", "이름을 수정할 분야 하나를 선택하세요."
            )
            return
        item = selected[0]
        name = self._category_name("연구분야 이름 수정", item.text())
        if name is None or name == item.text():
            return
        others = {
            self.focus_list.item(row).text().casefold()
            for row in range(self.focus_list.count())
            if self.focus_list.item(row) is not item
        }
        if name.casefold() in others:
            QMessageBox.warning(self, "중복 연구분야", "이미 같은 분야가 있습니다.")
            return
        item.setText(name)

    def _remove_focus_categories(self) -> None:
        selected = self.focus_list.selectedItems()
        if not selected:
            return
        if self.focus_list.count() - len(selected) < 1:
            QMessageBox.warning(
                self, "연구분야 필요", "연구분야를 하나 이상 남겨야 합니다."
            )
            return
        for item in selected:
            self.focus_list.takeItem(self.focus_list.row(item))

    def _add_watch_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "감시 폴더 추가", str(Path.home())
        )
        if not path:
            return
        normalized = str(Path(path).expanduser().resolve())
        existing = {
            self.watch_list.item(row).text().casefold()
            for row in range(self.watch_list.count())
        }
        if normalized.casefold() not in existing:
            self.watch_list.addItem(normalized)

    def _remove_watch_folders(self) -> None:
        for item in self.watch_list.selectedItems():
            self.watch_list.takeItem(self.watch_list.row(item))

    def _watch_folders(self) -> list[Path]:
        return [
            Path(self.watch_list.item(row).text())
            for row in range(self.watch_list.count())
        ]

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
        watch_folders = self._watch_folders()
        if not watch_folders:
            QMessageBox.warning(
                self, "감시 폴더 필요", "감시 폴더를 하나 이상 추가하세요."
            )
            return
        _old_input, old_library = self._controller.configured_paths()
        new_library = Path(self.library_edit.text().strip()).expanduser().resolve()
        if old_library.resolve() != new_library and old_library.exists():
            if QMessageBox.question(
                self,
                "라이브러리 폴더 이동",
                "라이브러리 폴더를 변경하면 PaperPack, 색인 DB, 분석 큐와 상태 파일을 "
                "모두 새 폴더로 옮깁니다. 파일이 많으면 시간이 오래 걸릴 수 있습니다.\n\n"
                "계속할까요?",
            ) != QMessageBox.Yes:
                return
        try:
            self._controller.save_paths(
                watch_folders[0],
                Path(self.library_edit.text().strip()),
                auto_enabled=self.auto_check.isChecked(),
                resource_profile=self.profile_combo.currentData(),
                scan_interval_seconds=self.interval_spin.value(),
                remove_source_after_import=self.remove_source_check.isChecked(),
                auto_organize_academic=self.auto_organize_check.isChecked(),
                research_categories=self._research_categories(),
                focus_categories=self._checked_focus_categories(),
                watch_folders=watch_folders,
            )
        except Exception as exc:
            QMessageBox.warning(self, "폴더 설정 실패", str(exc))
            return
        self.accept()
