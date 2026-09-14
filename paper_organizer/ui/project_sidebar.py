"""Library project navigation and editing controls."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from paper_organizer.ui.dialog_utils import suppress_context_help_button
from paper_organizer.ui.fluent_style import decorate_button


class ProjectSidebar(QWidget):
    project_changed = pyqtSignal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.project_id = ""
        self.projects = []
        self.setMinimumWidth(140)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.addWidget(QLabel("프로젝트"), 1)
        self.add_button = QToolButton()
        decorate_button(self.add_button, "add")
        self.add_button.setToolTip("프로젝트 생성")
        self.add_button.setFixedSize(28, 28)
        self.add_button.setStyleSheet("QToolButton { padding: 0; min-height: 0; }")
        self.add_button.clicked.connect(lambda: self.edit_project())
        header.addWidget(self.add_button)
        self.manage_button = QToolButton()
        decorate_button(self.manage_button, "menu")
        self.manage_button.setToolTip("프로젝트 관리")
        self.manage_button.setFixedSize(32, 28)
        self.manage_button.setStyleSheet("QToolButton { padding: 0 6px 0 0; min-height: 0; }")
        self.manage_button.setPopupMode(QToolButton.InstantPopup)
        self.manage_menu = QMenu(self.manage_button)
        self.manage_menu.aboutToShow.connect(self._update_manage_menu)
        self.manage_button.setMenu(self.manage_menu)
        header.addWidget(self.manage_button)
        layout.addLayout(header)
        self.list = QListWidget()
        self.list.setFlow(QListView.TopToBottom)
        self.list.setWrapping(False)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda item: self.edit_project(item.data(Qt.UserRole)) if item.data(Qt.UserRole) else None)
        layout.addWidget(self.list)

    def refresh(self, entries):
        self.projects = self.controller.list_projects()
        self.list.blockSignals(True)
        self.list.clear()
        counts = {}
        for entry in entries:
            for key in set(entry.record.get("curation", {}).get("project_ids", [])):
                counts[key] = counts.get(key, 0) + 1
        rows = [("", "전체 논문", "", len(entries))] + [
            (p["id"], p["name"], p["description"], counts.get(p["id"], 0))
            for p in sorted(self.projects, key=lambda p: p["name"].casefold())
        ]
        selected_row = 0
        for row, (key, name, description, count) in enumerate(rows):
            item = QListWidgetItem(f"{name}  ({count})")
            item.setData(Qt.UserRole, key)
            item.setToolTip(name + ("\n" + description if description else ""))
            self.list.addItem(item)
            if key == self.project_id:
                selected_row = row
        self.list.setCurrentRow(selected_row)
        self.project_id = self.list.currentItem().data(Qt.UserRole)
        self.list.blockSignals(False)

    def _selected(self, item, previous):
        self.project_id = item.data(Qt.UserRole) if item else ""
        self.project_changed.emit(self.project_id)

    def edit_project(self, project_id=""):
        project = next((p for p in self.projects if p["id"] == project_id), {})
        dialog = QDialog(self)
        dialog.setWindowTitle("프로젝트 수정" if project_id else "프로젝트 생성")
        suppress_context_help_button(dialog)
        dialog.resize(380, 240)
        form = QFormLayout(dialog)
        name = QLineEdit(project.get("name", ""))
        name.setMaxLength(80)
        description = QPlainTextEdit(project.get("description", ""))
        form.addRow("이름", name)
        form.addRow("설명", description)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("저장")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        form.addRow(buttons)
        def save():
            try:
                key = self.controller.save_project(name.text(), description.toPlainText(), project_id=project_id)
            except Exception as exc:
                QMessageBox.warning(dialog, "프로젝트 저장 실패", str(exc))
                return
            self.project_id = key
            dialog.accept()
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        name.setFocus()
        if dialog.exec_() == QDialog.Accepted:
            self.project_changed.emit(self.project_id)
            return self.project_id
        return ""

    def _update_manage_menu(self):
        self.manage_menu.clear()
        self.manage_menu.addAction("프로젝트 생성", lambda: self.edit_project())
        edit = self.manage_menu.addAction("프로젝트 수정", lambda: self.edit_project(self.project_id))
        delete = self.manage_menu.addAction("프로젝트 삭제", lambda: self._delete(self.project_id))
        edit.setEnabled(bool(self.project_id))
        delete.setEnabled(bool(self.project_id))

    def _menu(self, position):
        item = self.list.itemAt(position)
        key = item.data(Qt.UserRole) if item else ""
        menu = QMenu(self)
        menu.addAction("프로젝트 생성", lambda: self.edit_project())
        if key:
            menu.addAction("프로젝트 수정", lambda: self.edit_project(key))
            menu.addAction("프로젝트 삭제", lambda: self._delete(key))
        menu.exec_(self.list.viewport().mapToGlobal(position))

    def _delete(self, key):
        project = next(p for p in self.projects if p["id"] == key)
        if QMessageBox.question(self, "프로젝트 삭제", f"‘{project['name']}’ 프로젝트를 삭제할까요?\n논문과 PDF는 그대로 보관됩니다.", QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            self.controller.delete_project(key)
        except Exception as exc:
            QMessageBox.warning(self, "프로젝트 삭제 실패", str(exc))
            return
        if self.project_id == key:
            self.project_id = ""
        self.project_changed.emit(self.project_id)
