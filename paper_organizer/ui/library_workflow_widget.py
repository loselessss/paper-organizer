"""Collection review and editable JSON library widgets."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.library_workflow import (
    EditablePaperMetadata,
    LibraryEntry,
    LibraryWorkflowController,
    ReviewItem,
    ReviewScan,
)
from paper_organizer.integrations.spdf_bridge import open_pdf


class _ScanWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller

    def run(self) -> None:
        try:
            self.completed.emit(self._controller.scan())
        except Exception as exc:
            self.failed.emit(str(exc))


class MetadataForm(QGroupBox):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(title, parent)
        form = QFormLayout(self)
        self.title_edit = QLineEdit()
        self.authors_edit = QLineEdit()
        self.authors_edit.setPlaceholderText("쉼표로 구분")
        self.year_edit = QLineEdit()
        self.year_edit.setMaximumWidth(100)
        self.category_edit = QLineEdit("Uncategorized")
        self.subcategory_edit = QLineEdit("General")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("쉼표로 구분")
        self.summary_edit = QTextEdit()
        self.summary_edit.setMaximumHeight(105)
        form.addRow("제목", self.title_edit)
        form.addRow("저자", self.authors_edit)
        form.addRow("연도", self.year_edit)
        form.addRow("분야", self.category_edit)
        form.addRow("세부분야", self.subcategory_edit)
        form.addRow("태그", self.tags_edit)
        form.addRow("한국어 설명", self.summary_edit)

    def set_metadata(self, metadata: EditablePaperMetadata | None) -> None:
        value = metadata or EditablePaperMetadata()
        self.title_edit.setText(value.title)
        self.authors_edit.setText(", ".join(value.authors))
        self.year_edit.setText(str(value.year or ""))
        self.category_edit.setText(value.category)
        self.subcategory_edit.setText(value.subcategory)
        self.tags_edit.setText(", ".join(value.tags))
        self.summary_edit.setPlainText(value.summary_ko)
        self.setEnabled(metadata is not None)

    def metadata(self) -> EditablePaperMetadata:
        year_text = self.year_edit.text().strip()
        if year_text and not year_text.isdigit():
            raise ValueError("연도는 숫자로 입력하세요.")
        split_values = lambda text: [value.strip() for value in text.split(",") if value.strip()]
        return EditablePaperMetadata(
            title=self.title_edit.text().strip(),
            authors=split_values(self.authors_edit.text()),
            year=int(year_text) if year_text else None,
            category=self.category_edit.text().strip() or "Uncategorized",
            subcategory=self.subcategory_edit.text().strip() or "General",
            tags=split_values(self.tags_edit.text()),
            summary_ko=self.summary_edit.toPlainText().strip(),
        )


class CollectionReviewWidget(QWidget):
    library_changed = pyqtSignal()

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._items: list[ReviewItem] = []
        self._worker: _ScanWorker | None = None
        self._schedule_followup = False
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(15_000)
        self._auto_timer.timeout.connect(lambda: self.scan_now(False))

        root = QVBoxLayout(self)
        paths = QGroupBox("폴더 및 저전력 감시")
        path_form = QFormLayout(paths)
        input_row, self.input_edit = self._path_row(self._browse_input)
        library_row, self.library_edit = self._path_row(self._browse_library)
        sync_row, self.sync_edit = self._path_row(self._browse_sync, allow_clear=True)
        self.auto_check = QCheckBox("15초 간격으로 가볍게 검색 (분석은 안정된 새 PDF만 1회)")
        save_paths = QPushButton("폴더 설정 저장")
        save_paths.clicked.connect(self._save_paths)
        path_form.addRow("입력 폴더", input_row)
        path_form.addRow("PDF 라이브러리", library_row)
        path_form.addRow("OneDrive JSON 미러", sync_row)
        path_form.addRow("자동 감시", self.auto_check)
        path_form.addRow("", save_paths)
        root.addWidget(paths)

        action_row = QHBoxLayout()
        self.scan_button = QPushButton("새 PDF 검색")
        self.scan_button.clicked.connect(lambda: self.scan_now(True))
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        action_row.addWidget(self.scan_button)
        action_row.addWidget(self.status_label, 1)
        root.addLayout(action_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["파일", "판정", "중복", "추정 제목"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(self.table, 1)

        self.detail_label = QLabel("검토할 PDF를 선택하세요.")
        self.detail_label.setWordWrap(True)
        root.addWidget(self.detail_label)
        self.form = MetadataForm("이동 전에 수정할 색인")
        self.form.set_metadata(None)
        root.addWidget(self.form)

        review_actions = QHBoxLayout()
        self.open_button = QPushButton("sPDF로 열기")
        self.organize_button = QPushButton("승인 후 라이브러리로 이동")
        self.trash_button = QPushButton("확인된 중복을 앱 휴지통으로 이동")
        self.restore_button = QPushButton("앱 휴지통에서 복원…")
        self.open_button.clicked.connect(self._open_selected)
        self.organize_button.clicked.connect(self._organize_selected)
        self.trash_button.clicked.connect(self._trash_selected)
        self.restore_button.clicked.connect(self._restore_trash)
        for button in (self.open_button, self.organize_button, self.trash_button):
            button.setEnabled(False)
            review_actions.addWidget(button)
        review_actions.addWidget(self.restore_button)
        review_actions.addStretch(1)
        root.addLayout(review_actions)
        self._load_settings()

    def _path_row(self, browse_slot, *, allow_clear: bool = False):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        browse = QPushButton("찾아보기…")
        browse.clicked.connect(browse_slot)
        layout.addWidget(edit, 1)
        layout.addWidget(browse)
        if allow_clear:
            clear = QPushButton("사용 안 함")
            clear.clicked.connect(edit.clear)
            layout.addWidget(clear)
        return container, edit

    def _load_settings(self) -> None:
        input_dir, library_root = self._controller.configured_paths()
        settings = self._controller.settings()
        self.input_edit.setText(str(input_dir))
        self.library_edit.setText(str(library_root))
        self.sync_edit.setText(settings.metadata_sync_dir)
        self.auto_check.setChecked(settings.auto_enabled)
        self._set_auto_enabled(settings.auto_enabled)

    def _browse_input(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "입력 폴더 선택", self.input_edit.text())
        if path:
            self.input_edit.setText(path)

    def _browse_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "PDF 라이브러리 선택", self.library_edit.text())
        if path:
            self.library_edit.setText(path)

    def _browse_sync(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "OneDrive 안의 JSON 미러 폴더 선택", self.sync_edit.text()
        )
        if path:
            self.sync_edit.setText(path)

    def _save_paths(self) -> None:
        try:
            sync_text = self.sync_edit.text().strip()
            self._controller.save_paths(
                Path(self.input_edit.text().strip()),
                Path(self.library_edit.text().strip()),
                auto_enabled=self.auto_check.isChecked(),
                metadata_sync_dir=Path(sync_text) if sync_text else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "폴더 설정 실패", str(exc))
            return
        self._set_auto_enabled(self.auto_check.isChecked())
        self.status_label.setText("폴더 설정을 저장했습니다.")

    def _set_auto_enabled(self, enabled: bool) -> None:
        if enabled:
            self._auto_timer.start()
        else:
            self._auto_timer.stop()

    def scan_now(self, schedule_followup: bool = True) -> None:
        if self.is_busy():
            return
        self._schedule_followup = schedule_followup
        self.scan_button.setEnabled(False)
        self.status_label.setText("PDF 안정성과 본문 지문을 확인하고 있습니다…")
        worker = _ScanWorker(self._controller, self)
        worker.completed.connect(self._scan_ready)
        worker.failed.connect(self._scan_failed)
        worker.finished.connect(self._scan_finished)
        self._worker = worker
        worker.start()

    def _scan_ready(self, result: ReviewScan) -> None:
        self._items = list(result.items)
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            duplicate = item.duplicate
            duplicate_text = "없음"
            if duplicate:
                duplicate_text = f"{duplicate.match.kind.value} ({duplicate.match.score:.2f})"
            values = [item.path.name, item.detection_status, duplicate_text, item.metadata.title]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        problem_text = f" · 오류 {len(result.problems)}개" if result.problems else ""
        self.status_label.setText(
            f"검토 대상 {len(result.items)}개 · 안정성 확인 중 {result.pending_stability}개{problem_text}"
        )
        if result.pending_stability and self._schedule_followup:
            self.status_label.setText(self.status_label.text() + " · 잠시 후 한 번 더 확인합니다.")
            QTimer.singleShot(1500, lambda: self.scan_now(False))

    def _scan_failed(self, message: str) -> None:
        self.status_label.setText(f"검색 실패: {message}")
        QMessageBox.warning(self, "PDF 검색 실패", message)

    def _scan_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker:
            worker.deleteLater()
        self.scan_button.setEnabled(True)

    def _selected(self) -> ReviewItem | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _selection_changed(self) -> None:
        item = self._selected()
        self.form.set_metadata(item.metadata if item else None)
        enabled = item is not None
        self.open_button.setEnabled(enabled)
        self.organize_button.setEnabled(enabled)
        confirmed = bool(item and item.duplicate and item.duplicate.confirmed)
        self.trash_button.setEnabled(confirmed)
        if not item:
            self.detail_label.setText("검토할 PDF를 선택하세요.")
            return
        detail = f"{item.detection_reason}\nwork_id: {item.identity.work_id}"
        if item.identity.wrapper_pages:
            pages = ", ".join(str(page.pdf_page) for page in item.identity.wrapper_pages)
            detail += f"\nResearchGate/저장소 표지 후보 페이지: {pages}"
        if item.duplicate:
            detail += (
                f"\n중복 후보: {item.duplicate.title} [{item.duplicate.source_variant}]"
                f"\n{'; '.join(item.duplicate.match.reasons)}"
            )
        self.detail_label.setText(detail)

    def _open_selected(self) -> None:
        item = self._selected()
        if item:
            try:
                open_pdf(item.path, self)
            except Exception as exc:
                QMessageBox.warning(self, "sPDF 열기 실패", str(exc))

    def _organize_selected(self) -> None:
        item = self._selected()
        if item is None:
            return
        if item.detection_status != "academic_likely" and QMessageBox.question(
            self,
            "수동 승인 확인",
            "학술 논문으로 확실히 판정되지 않았습니다. 그래도 승인하여 이동할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            result = self._controller.organize(item, self.form.metadata())
        except Exception as exc:
            QMessageBox.warning(self, "논문 이동 실패", str(exc))
            return
        message = f"이동 완료: {result.pdf_path}"
        if result.sync_warning:
            message += f"\nJSON 미러 경고: {result.sync_warning}"
        QMessageBox.information(self, "논문 정리 완료", message)
        self.library_changed.emit()
        self.scan_now(False)

    def _trash_selected(self) -> None:
        item = self._selected()
        if item is None or item.duplicate is None or not item.duplicate.confirmed:
            return
        if QMessageBox.question(
            self,
            "중복 파일 이동",
            "이 파일은 자동 영구 삭제되지 않습니다. 복구 가능한 앱 휴지통으로 이동할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            operation = self._controller.trash_confirmed_duplicate(item)
        except Exception as exc:
            QMessageBox.warning(self, "중복 이동 실패", str(exc))
            return
        QMessageBox.information(
            self, "앱 휴지통 이동 완료", f"작업 ID: {operation.operation_id}"
        )
        self.scan_now(False)

    def _restore_trash(self) -> None:
        entries = self._controller.list_trash()
        if not entries:
            QMessageBox.information(self, "앱 휴지통", "복원할 중복 파일이 없습니다.")
            return
        labels = [f"{entry.operation_id} · {entry.original_path.name}" for entry in entries]
        selected, accepted = QInputDialog.getItem(
            self, "중복 파일 복원", "복원할 작업", labels, 0, False
        )
        if not accepted:
            return
        entry = entries[labels.index(selected)]
        try:
            restored = self._controller.restore_trash(entry)
        except Exception as exc:
            QMessageBox.warning(self, "복원 실패", str(exc))
            return
        QMessageBox.information(self, "복원 완료", f"복원 위치: {restored}")
        self.scan_now(True)

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()


class LibraryWidget(QWidget):
    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._entries: list[LibraryEntry] = []
        root = QVBoxLayout(self)
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("제목, 저자, 연도, 분야, 태그, 설명 검색")
        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(lambda: self.refresh(True))
        self.search_edit.returnPressed.connect(self.refresh)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(refresh_button)
        root.addLayout(search_row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["제목", "저자", "연도", "분야", "판본"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._open_selected())
        root.addWidget(self.table, 1)
        self.form = MetadataForm("선택한 논문의 JSON 색인 편집")
        self.form.set_metadata(None)
        root.addWidget(self.form)
        actions = QHBoxLayout()
        self.save_button = QPushButton("수정 저장 및 재색인")
        self.open_button = QPushButton("sPDF로 열기")
        self.save_button.clicked.connect(self._save_selected)
        self.open_button.clicked.connect(self._open_selected)
        self.save_button.setEnabled(False)
        self.open_button.setEnabled(False)
        actions.addWidget(self.save_button)
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        root.addLayout(actions)
        self.status_label = QLabel()
        root.addWidget(self.status_label)
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        if force:
            self._controller.invalidate_library_cache()
        try:
            self._entries = self._controller.list_library(self.search_edit.text())
        except Exception as exc:
            self.status_label.setText(f"라이브러리 읽기 실패: {exc}")
            return
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            metadata = entry.metadata
            values = [
                metadata.title,
                ", ".join(metadata.authors),
                str(metadata.year or ""),
                f"{metadata.category} / {metadata.subcategory}",
                entry.source_variant,
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.form.set_metadata(None)
        self.save_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.status_label.setText(f"논문 파일 {len(self._entries)}개")

    def _selected(self) -> LibraryEntry | None:
        row = self.table.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def _selection_changed(self) -> None:
        entry = self._selected()
        self.form.set_metadata(entry.metadata if entry else None)
        self.save_button.setEnabled(entry is not None)
        self.open_button.setEnabled(bool(entry and entry.pdf_path.is_file()))

    def _save_selected(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        try:
            updated = self._controller.update_library_metadata(entry, self.form.metadata())
        except Exception as exc:
            QMessageBox.warning(self, "색인 저장 실패", str(exc))
            return
        self._entries[self.table.currentRow()] = updated
        status = "sidecar JSON 저장 및 통합 인덱스 재생성을 완료했습니다."
        if updated.sync_warning:
            status += f" OneDrive JSON 미러 경고: {updated.sync_warning}"
        self.refresh()
        self.status_label.setText(status)

    def _open_selected(self) -> None:
        entry = self._selected()
        if entry:
            try:
                open_pdf(entry.pdf_path, self)
            except Exception as exc:
                QMessageBox.warning(self, "sPDF 열기 실패", str(exc))
