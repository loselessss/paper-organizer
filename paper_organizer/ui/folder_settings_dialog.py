"""Configure summary watch folders, automation and research categories."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
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
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.lifecycle import LifecycleSettingsController
from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.core.classifier import (
    TaxonomyError,
    taxonomy_category_names,
    taxonomy_subcategory_names,
)
from paper_organizer.ui.dialog_utils import suppress_context_help_button


class FolderSettingsDialog(QDialog):
    """Save paths and watch preferences through the workflow controller."""

    def __init__(
        self,
        controller: LibraryWorkflowController,
        parent=None,
        *,
        lifecycle: LifecycleSettingsController | None = None,
    ) -> None:
        super().__init__(parent)
        suppress_context_help_button(self)
        self._controller = controller
        self._lifecycle = lifecycle
        self._subcategory_overrides: dict[str, list[str]] = {}
        self.setWindowTitle("요약 감시 옵션")
        self.setMinimumWidth(920)

        root = QVBoxLayout(self)
        content = QHBoxLayout()
        content.setSpacing(12)
        watch_panel = QWidget()
        watch_panel.setObjectName("watchSettingsPanel")
        watch_panel.setMinimumWidth(0)
        watch_root = QVBoxLayout(watch_panel)
        watch_root.setContentsMargins(0, 0, 0, 0)
        categories_panel = QWidget()
        categories_panel.setObjectName("researchCategoriesPanel")
        categories_panel.setMinimumWidth(500)
        categories_root = QVBoxLayout(categories_panel)
        categories_root.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        library_row, self.library_edit = self._path_row(self._browse_library)

        watch_group = QGroupBox("요약할 논문·특허 감시 폴더")
        watch_layout = QVBoxLayout(watch_group)
        self.watch_list = QListWidget()
        self.watch_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.watch_list.setMaximumHeight(96)
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
        watch_root.addWidget(watch_group)
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("저사양/절전", "eco")
        self.profile_combo.addItem("균형", "balanced")
        self.profile_combo.addItem("고성능", "performance")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 3600)
        self.interval_spin.setSuffix("초")
        self.interval_spin.setToolTip("5초에서 1시간 사이로 설정할 수 있습니다.")
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.auto_check = QCheckBox("자동 검색")
        self.auto_check.setToolTip(
            "설정한 주기로 가볍게 검색하고 안정된 새 PDF만 1회 분석합니다."
        )
        self.watch_subdirectories_check = QCheckBox("하위 폴더 포함")
        self.watch_subdirectories_check.setToolTip(
            "선택한 모든 감시 폴더 아래를 재귀적으로 검색합니다. "
            "PaperPack 라이브러리는 감시 폴더 밖에 있어야 합니다."
        )
        self.remove_source_check = QCheckBox("원본 PDF 삭제")
        self.remove_source_check.setToolTip(
            "기본값은 원본 유지입니다. 삭제 실패 시 새 paperpack을 롤백합니다."
        )
        self.auto_organize_check = QCheckBox("논문 자동 보관")
        self.auto_organize_check.setToolTip(
            "중복 후보가 있거나 판정이 불확실한 PDF는 자동 보관하지 않고 "
            "수집 화면에 남겨 사람이 검토합니다."
        )
        form.addRow("PaperPack 라이브러리", library_row)
        form.addRow("시스템 부하", self.profile_combo)
        form.addRow("스캔 주기", self.interval_spin)
        form.addRow("자동 감시", self.auto_check)
        form.addRow("검색 범위", self.watch_subdirectories_check)
        form.addRow("자동 보관", self.auto_organize_check)
        form.addRow("입력 PDF", self.remove_source_check)
        watch_root.addLayout(form)

        if self._lifecycle is not None:
            lifecycle_group = QGroupBox("시작/종료")
            lifecycle_layout = QVBoxLayout(lifecycle_group)
            self.start_with_windows_check = QCheckBox(
                "Windows 로그인 시 자동 시작"
            )
            self.start_with_windows_check.setToolTip(
                "현재 사용자 계정에만 적용되며 관리자 권한이 필요하지 않습니다."
            )
            lifecycle_layout.addWidget(self.start_with_windows_check)
            self.background_radio = QRadioButton(
                "X 버튼: 백그라운드 유지"
            )
            self.quit_radio = QRadioButton("X 버튼: 완전 종료")
            self.close_button_group = QButtonGroup(self)
            self.close_button_group.addButton(self.background_radio)
            self.close_button_group.addButton(self.quit_radio)
            lifecycle_layout.addWidget(self.background_radio)
            lifecycle_layout.addWidget(self.quit_radio)
            watch_root.addWidget(lifecycle_group)
        else:
            self.start_with_windows_check = None
            self.background_radio = None
            self.quit_radio = None
            self.close_button_group = None

        focus_group = QGroupBox("연구분야 관리")
        focus_group.setObjectName("researchCategoryGroup")
        focus_group.setMinimumWidth(500)
        focus_layout = QVBoxLayout(focus_group)
        focus_note = QLabel(
            "체크한 분야 안에서만 자동 분류합니다. 아무것도 체크하지 않으면 "
            "전체 분야를 사용합니다."
        )
        focus_note.setWordWrap(True)
        focus_layout.addWidget(focus_note)
        focus_lists = QHBoxLayout()
        category_column = QWidget()
        category_column_layout = QVBoxLayout(category_column)
        category_column_layout.setContentsMargins(0, 0, 0, 0)
        subcategory_column = QWidget()
        subcategory_column_layout = QVBoxLayout(subcategory_column)
        subcategory_column_layout.setContentsMargins(0, 0, 0, 0)
        self.focus_list_label = QLabel("분야 선택 ↓")
        self.focus_list_label.setToolTip("분야를 누르면 오른쪽에 세부분야가 표시됩니다.")
        self.subcategory_list_label = QLabel("세부분야")
        self.focus_list = QListWidget()
        self.focus_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.focus_list.setMinimumHeight(220)
        self.focus_list.setMinimumWidth(260)
        self.focus_list.setToolTip("분야를 선택하면 연결된 세부분야를 볼 수 있습니다.")
        self.subcategory_list = QListWidget()
        self.subcategory_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.subcategory_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.subcategory_list.setMinimumHeight(220)
        self.subcategory_list.setMinimumWidth(200)
        category_column_layout.addWidget(self.focus_list_label)
        category_column_layout.addWidget(self.focus_list, 1)
        category_actions = QHBoxLayout()
        self.add_category_button = QPushButton("분야 추가")
        self.edit_category_button = QPushButton("이름 수정")
        self.remove_category_button = QPushButton("선택 삭제")
        self.add_category_button.clicked.connect(self._add_focus_category)
        self.edit_category_button.clicked.connect(self._edit_focus_category)
        self.remove_category_button.clicked.connect(self._remove_focus_categories)
        category_actions.addWidget(self.add_category_button)
        category_actions.addWidget(self.edit_category_button)
        category_actions.addWidget(self.remove_category_button)
        category_actions.addStretch(1)
        category_column_layout.addLayout(category_actions)
        subcategory_column_layout.addWidget(self.subcategory_list_label)
        subcategory_column_layout.addWidget(self.subcategory_list, 1)
        subcategory_actions = QHBoxLayout()
        self.add_subcategory_button = QPushButton("세부분야 추가")
        self.remove_subcategory_button = QPushButton("선택 삭제")
        self.add_subcategory_button.clicked.connect(self._add_subcategory)
        self.remove_subcategory_button.clicked.connect(self._remove_subcategories)
        subcategory_actions.addWidget(self.add_subcategory_button)
        subcategory_actions.addWidget(self.remove_subcategory_button)
        subcategory_actions.addStretch(1)
        subcategory_column_layout.addLayout(subcategory_actions)
        focus_lists.addWidget(category_column, 3)
        focus_lists.addWidget(subcategory_column, 2)
        focus_layout.addLayout(focus_lists)
        self.focus_list.currentItemChanged.connect(
            lambda current, _previous: self._show_subcategories(current)
        )
        self.focus_list.itemDoubleClicked.connect(
            lambda _item: self._edit_focus_category()
        )
        categories_root.addWidget(focus_group)
        content.addWidget(watch_panel, 2)
        content.addWidget(categories_panel, 3)
        root.addLayout(content, 1)

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
        self.watch_subdirectories_check.setChecked(settings.watch_subdirectories)
        self.remove_source_check.setChecked(settings.remove_source_after_import)
        self.auto_organize_check.setChecked(settings.auto_organize_academic)
        if self._lifecycle is not None:
            lifecycle_settings = self._lifecycle.settings()
            self.start_with_windows_check.setChecked(
                lifecycle_settings.start_with_windows
            )
            if lifecycle_settings.close_behavior == "background":
                self.background_radio.setChecked(True)
            else:
                self.quit_radio.setChecked(True)
        self._load_focus_categories(
            settings.focus_categories,
            settings.research_categories,
            settings.research_subcategories,
        )

    def _load_focus_categories(
        self,
        selected: list[str],
        configured: list[str],
        subcategory_overrides: dict[str, list[str]],
    ) -> None:
        self._subcategory_overrides = {
            category.strip(): self._normalized_names(subcategories)
            for category, subcategories in subcategory_overrides.items()
            if category.strip()
        }
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
        if self.focus_list.count():
            self.focus_list.setCurrentRow(0)
        else:
            self._show_subcategories(None)

    def _show_subcategories(self, item: QListWidgetItem | None) -> None:
        self.subcategory_list.clear()
        name = item.text().strip() if item is not None else ""
        self.add_subcategory_button.setEnabled(bool(name))
        self.remove_subcategory_button.setEnabled(bool(name))
        if not name:
            self.subcategory_list.addItem("분야를 선택하세요.")
            return
        subcategories = self._subcategories_for_category(name)
        if not subcategories:
            disabled = QListWidgetItem("세부분야 없음")
            disabled.setFlags(Qt.NoItemFlags)
            self.subcategory_list.addItem(disabled)
            return
        for subcategory in subcategories:
            self.subcategory_list.addItem(subcategory)

    def _bundled_subcategories(self, category: str) -> list[str]:
        try:
            return taxonomy_subcategory_names(category)
        except TaxonomyError:
            return []

    def _subcategories_for_category(self, category: str) -> list[str]:
        if category in self._subcategory_overrides:
            return list(self._subcategory_overrides[category])
        return self._bundled_subcategories(category)

    def _set_subcategories_for_category(
        self, category: str, subcategories: list[str]
    ) -> None:
        normalized = self._normalized_names(subcategories)
        if normalized == self._bundled_subcategories(category):
            self._subcategory_overrides.pop(category, None)
        else:
            self._subcategory_overrides[category] = normalized

    def _normalized_names(self, values: list[str]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for value in values:
            name = " ".join(str(value).split())
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

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

    def _research_subcategories(self) -> dict[str, list[str]]:
        categories = set(self._research_categories())
        return {
            category: subcategories
            for category, subcategories in self._subcategory_overrides.items()
            if category in categories
        }

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

    def _subcategory_name(self, title: str, value: str = "") -> str | None:
        name, accepted = QInputDialog.getText(
            self,
            title,
            "세부분야 이름",
            text=value,
        )
        if not accepted:
            return None
        normalized = " ".join(name.split())
        if not normalized:
            QMessageBox.warning(self, "세부분야 이름 필요", "이름을 입력하세요.")
            return None
        if len(normalized) > 80 or "," in normalized:
            QMessageBox.warning(
                self,
                "세부분야 이름 확인",
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
        self.focus_list.clearSelection()
        self.focus_list.addItem(entry)
        self.focus_list.setCurrentItem(entry)
        entry.setSelected(True)
        self._set_subcategories_for_category(name, [])
        self._show_subcategories(entry)

    def _edit_focus_category(self) -> None:
        selected = self.focus_list.selectedItems()
        if not selected and self.focus_list.currentItem() is not None:
            selected = [self.focus_list.currentItem()]
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
        old_name = item.text()
        current_subcategories = self._subcategories_for_category(old_name)
        self._subcategory_overrides.pop(old_name, None)
        item.setText(name)
        self._set_subcategories_for_category(name, current_subcategories)
        self._show_subcategories(item)

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
            self._subcategory_overrides.pop(item.text().strip(), None)
            self.focus_list.takeItem(self.focus_list.row(item))
        current = self.focus_list.currentItem()
        if current is None and self.focus_list.count():
            self.focus_list.setCurrentRow(0)
            current = self.focus_list.currentItem()
        self._show_subcategories(current)

    def _add_subcategory(self) -> None:
        current = self.focus_list.currentItem()
        if current is None:
            return
        category = current.text().strip()
        name = self._subcategory_name("세부분야 추가")
        if name is None:
            return
        subcategories = self._subcategories_for_category(category)
        if name.casefold() in {value.casefold() for value in subcategories}:
            QMessageBox.warning(self, "중복 세부분야", "이미 같은 세부분야가 있습니다.")
            return
        self._set_subcategories_for_category(category, [*subcategories, name])
        self._show_subcategories(current)
        matches = self.subcategory_list.findItems(name, Qt.MatchExactly)
        if matches:
            self.subcategory_list.setCurrentItem(matches[0])
            matches[0].setSelected(True)

    def _remove_subcategories(self) -> None:
        current = self.focus_list.currentItem()
        if current is None:
            return
        selected = self.subcategory_list.selectedItems()
        if not selected:
            return
        category = current.text().strip()
        removed = {item.text().casefold() for item in selected}
        remaining = [
            value
            for value in self._subcategories_for_category(category)
            if value.casefold() not in removed
        ]
        self._set_subcategories_for_category(category, remaining)
        self._show_subcategories(current)

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
                research_subcategories=self._research_subcategories(),
                focus_categories=self._checked_focus_categories(),
                watch_folders=watch_folders,
                watch_subdirectories=self.watch_subdirectories_check.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "폴더 설정 실패", str(exc))
            return
        if self._lifecycle is not None:
            close_behavior = (
                "background" if self.background_radio.isChecked() else "quit"
            )
            try:
                self._lifecycle.save_preferences(
                    start_with_windows=self.start_with_windows_check.isChecked(),
                    close_behavior=close_behavior,
                )
            except Exception as exc:
                QMessageBox.warning(self, "시작 및 종료 설정 실패", str(exc))
                return
        self.accept()
