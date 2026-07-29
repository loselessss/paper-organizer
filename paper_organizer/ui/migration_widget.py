"""GUI for safe legacy PDF/JSON to paperpack migration."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.library_workflow import LibraryWorkflowController


class _MigrationWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        controller: LibraryWorkflowController,
        metadata_paths: list[Path],
        move_legacy_to_trash: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._metadata_paths = metadata_paths
        self._move_legacy_to_trash = move_legacy_to_trash

    def run(self) -> None:
        try:
            result = self._controller.migrate_legacy_papers(
                self._metadata_paths,
                move_legacy_to_trash=self._move_legacy_to_trash,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class LegacyMigrationWidget(QWidget):
    library_changed = pyqtSignal()

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._candidates = []
        self._worker: _MigrationWorker | None = None

        root = QVBoxLayout(self)
        note = QLabel(
            "기존 PDF + paper.json (+ content.json)을 표준 ZIP paperpack으로 변환합니다. "
            "기본값은 기존 파일 유지이며, 선택 시 검증 완료 후 앱 휴지통으로 이동합니다."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["제목", "PDF", "메타데이터", "본문 색인"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        self.move_to_trash_check = QCheckBox(
            "변환 성공 후 기존 PDF와 색인 파일을 앱 휴지통으로 이동"
        )
        root.addWidget(self.move_to_trash_check)

        actions = QHBoxLayout()
        self.refresh_button = QPushButton("레거시 파일 다시 검색")
        self.migrate_button = QPushButton("선택 항목을 paperpack으로 변환")
        self.restore_button = QPushButton("마이그레이션 원본 복원…")
        self.refresh_button.clicked.connect(self.refresh)
        self.migrate_button.clicked.connect(self._migrate_selected)
        self.restore_button.clicked.connect(self._restore_legacy)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.migrate_button)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)
        root.addLayout(actions)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self.migrate_button.setEnabled(False)
        self.status_label.setText("레거시 파일 다시 검색을 눌러 변환 대상을 확인하세요.")

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def refresh(self) -> None:
        if self.is_busy():
            return
        try:
            preview = self._controller.legacy_migration_preview()
        except Exception as exc:
            self.status_label.setText(f"마이그레이션 검색 실패: {exc}")
            return
        self._candidates = list(preview.candidates)
        self.table.setRowCount(len(self._candidates))
        for row, item in enumerate(self._candidates):
            values = [
                item.title,
                str(item.pdf_path),
                str(item.metadata_path),
                str(item.content_path) if item.content_path else "없음",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if self._candidates:
            self.table.selectAll()
        details = [f"변환 가능 {len(self._candidates)}개"]
        if preview.already_migrated:
            details.append(f"이미 변환됨 {preview.already_migrated}개")
        if preview.problems:
            details.append(f"확인 필요 {len(preview.problems)}개")
            first = preview.problems[0]
            details.append(f"첫 오류: {first.path.name} — {first.message}")
        self.status_label.setText(" · ".join(details))
        self.migrate_button.setEnabled(bool(self._candidates))

    def _selected_paths(self) -> list[Path]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._candidates[row].metadata_path for row in rows]

    def _migrate_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "선택 필요", "변환할 항목을 선택하세요.")
            return
        move_to_trash = self.move_to_trash_check.isChecked()
        if move_to_trash and QMessageBox.question(
            self,
            "기존 파일 이동 확인",
            f"{len(paths)}개 논문의 paperpack 변환이 모두 성공한 뒤 기존 PDF와 색인 파일을 "
            "복구 가능한 앱 휴지통으로 이동할까요?",
        ) != QMessageBox.Yes:
            return
        self.refresh_button.setEnabled(False)
        self.migrate_button.setEnabled(False)
        self.status_label.setText(f"{len(paths)}개 항목을 변환·검증하고 있습니다…")
        worker = _MigrationWorker(self._controller, paths, move_to_trash, self)
        worker.completed.connect(self._migration_completed)
        worker.failed.connect(self._migration_failed)
        worker.finished.connect(self._migration_finished)
        self._worker = worker
        worker.start()

    def _migration_completed(self, result) -> None:
        message = f"PaperPack {len(result.items)}개 변환 완료"
        if result.legacy_moved_to_trash:
            message += f" · 앱 휴지통 작업 {result.trash_operation_id}"
        self.status_label.setText(message)
        self.library_changed.emit()

    def _migration_failed(self, message: str) -> None:
        self.status_label.setText(f"마이그레이션 실패: {message}")
        QMessageBox.warning(self, "마이그레이션 실패", message)

    def _migration_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker:
            worker.deleteLater()
        self.refresh_button.setEnabled(True)
        self.refresh()

    def _restore_legacy(self) -> None:
        try:
            entries = self._controller.legacy_migration_trash()
        except Exception as exc:
            QMessageBox.warning(self, "복원 목록 실패", str(exc))
            return
        if not entries:
            QMessageBox.information(self, "복원할 항목 없음", "복원할 마이그레이션 원본이 없습니다.")
            return
        labels = [
            f"{entry.created_at} · {entry.file_count}개 · {entry.operation_id}"
            for entry in entries
        ]
        selected, accepted = QInputDialog.getItem(
            self, "마이그레이션 원본 복원", "복원할 작업", labels, 0, False
        )
        if not accepted:
            return
        entry = entries[labels.index(selected)]
        if QMessageBox.question(
            self,
            "원본 복원 확인",
            "기존 PDF와 색인 파일을 원래 위치로 복원합니다. 변환된 paperpack은 유지됩니다.",
        ) != QMessageBox.Yes:
            return
        try:
            restored = self._controller.restore_legacy_migration(entry.operation_id)
        except Exception as exc:
            QMessageBox.warning(self, "원본 복원 실패", str(exc))
            return
        QMessageBox.information(
            self, "원본 복원 완료", f"기존 파일 {len(restored)}개를 복원했습니다."
        )
        self.library_changed.emit()
        self.refresh()


class LegacyMigrationDialog(QDialog):
    """도구 메뉴에서 여는 레거시 라이브러리 변환 다이얼로그."""

    library_changed = pyqtSignal()

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("레거시 라이브러리 변환")
        self.setMinimumSize(760, 480)
        layout = QVBoxLayout(self)
        self.widget = LegacyMigrationWidget(controller, self)
        self.widget.library_changed.connect(self.library_changed)
        layout.addWidget(self.widget)
        close_row = QHBoxLayout()
        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.reject)
        close_row.addStretch(1)
        close_row.addWidget(close_button)
        layout.addLayout(close_row)
        self.widget.refresh()

    def reject(self) -> None:
        if self.widget.is_busy():
            QMessageBox.information(
                self, "변환 진행 중", "레거시 변환이 끝난 뒤 창을 닫으세요."
            )
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.widget.is_busy():
            event.ignore()
            return
        super().closeEvent(event)
