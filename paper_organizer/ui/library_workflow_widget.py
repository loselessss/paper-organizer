"""Collection review and editable JSON library widgets."""

from __future__ import annotations

import html
from pathlib import Path
from threading import Event

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
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
from paper_organizer.application.analysis_queue import AnalysisQueueItem
from paper_organizer.application.background_analysis import (
    AnalysisRunEvent,
    BackgroundAnalysisService,
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


class _BackgroundAnalysisWorker(QThread):
    event = pyqtSignal(object)
    queue_changed = pyqtSignal()

    def __init__(self, service: BackgroundAnalysisService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._stop = Event()
        self._wake = Event()
        self._processing = False

    def request_stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_wake(self) -> None:
        self._wake.set()

    def is_processing(self) -> bool:
        return self._processing

    def run(self) -> None:
        try:
            recovered = self._service.recover_interrupted()
            if recovered:
                self.queue_changed.emit()
        except Exception as exc:
            self.event.emit(AnalysisRunEvent("failed", str(exc)))
            return
        while not self._stop.is_set():
            self._processing = True
            result = self._service.run_next(on_start=self._notify_started)
            self._processing = False
            self.event.emit(result)
            if result.state in {"completed", "failed"}:
                self.queue_changed.emit()
            if result.state == "disabled":
                break
            self._wake.wait(self._service.poll_interval())
            self._wake.clear()
        self._processing = False

    def _notify_started(self, event: AnalysisRunEvent) -> None:
        self.event.emit(event)
        self.queue_changed.emit()


class MetadataForm(QGroupBox):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(title, parent)
        form = QFormLayout(self)
        self.title_edit = QLineEdit()
        self.authors_edit = QLineEdit()
        self.authors_edit.setPlaceholderText("쉼표로 구분")
        self.year_edit = QLineEdit()
        self.year_edit.setMaximumWidth(100)
        self.venue_edit = QLineEdit()
        self.venue_edit.setPlaceholderText("저널명 또는 학회명")
        self.category_edit = QLineEdit("Uncategorized")
        self.subcategory_edit = QLineEdit("General")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("쉼표로 구분")
        self._summary_ko = ""
        form.addRow("제목", self.title_edit)
        form.addRow("저자", self.authors_edit)
        form.addRow("연도", self.year_edit)
        form.addRow("저널/학회", self.venue_edit)
        form.addRow("분야", self.category_edit)
        form.addRow("세부분야", self.subcategory_edit)
        form.addRow("태그", self.tags_edit)

    def set_metadata(self, metadata: EditablePaperMetadata | None) -> None:
        value = metadata or EditablePaperMetadata()
        self.title_edit.setText(value.title)
        self.authors_edit.setText(", ".join(value.authors))
        self.year_edit.setText(str(value.year or ""))
        self.venue_edit.setText(value.venue)
        self.category_edit.setText(value.category)
        self.subcategory_edit.setText(value.subcategory)
        self.tags_edit.setText(", ".join(value.tags))
        self._summary_ko = value.summary_ko
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
            venue=self.venue_edit.text().strip(),
            category=self.category_edit.text().strip() or "Uncategorized",
            subcategory=self.subcategory_edit.text().strip() or "General",
            tags=split_values(self.tags_edit.text()),
            summary_ko=self._summary_ko,
        )


class CollectionReviewWidget(QWidget):
    library_changed = pyqtSignal()
    queue_changed = pyqtSignal()
    papers_auto_organized = pyqtSignal(list)

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._items: list[ReviewItem] = []
        self._worker: _ScanWorker | None = None
        self._schedule_followup = False
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(lambda: self.scan_now(False))

        root = QVBoxLayout(self)
        action_row = QHBoxLayout()
        self.scan_button = QPushButton("새 PDF 검색")
        self.scan_button.clicked.connect(lambda: self.scan_now(True))
        self.settings_button = QPushButton("폴더 및 감시 설정…")
        self.settings_button.clicked.connect(self._show_folder_settings)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        action_row.addWidget(self.scan_button)
        action_row.addWidget(self.settings_button)
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
        self.organize_button = QPushButton("승인 후 paperpack으로 보관")
        self.trash_button = QPushButton("새 PDF 삭제 (앱 휴지통)")
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
        self._reload_watch_settings()

    def _reload_watch_settings(self) -> None:
        """설정 파일 기준으로 자동 감시 타이머를 다시 맞춘다."""
        settings = self._controller.settings()
        self._auto_timer.setInterval(max(5, settings.scan_interval_seconds) * 1000)
        if settings.auto_enabled:
            self._auto_timer.start()
        else:
            self._auto_timer.stop()

    def _show_folder_settings(self) -> None:
        from .folder_settings_dialog import FolderSettingsDialog

        dialog = FolderSettingsDialog(self._controller, self)
        if dialog.exec_():
            self._reload_watch_settings()
            self.status_label.setText("폴더 설정을 저장했습니다.")

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
            detection_labels = {
                "academic_likely": "학술 논문",
                "patent_likely": "특허",
                "needs_ocr": "OCR 필요",
                "needs_review": "검토 필요",
            }
            values = [
                item.path.name,
                detection_labels.get(item.detection_status, item.detection_status),
                duplicate_text,
                item.metadata.title,
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        problem_text = f" · 오류 {len(result.problems)}개" if result.problems else ""
        auto_text = (
            f" · 자동 보관 {len(result.auto_organized)}개" if result.auto_organized else ""
        )
        self.status_label.setText(
            f"검토 대상 {len(result.items)}개 · 안정성 확인 중 "
            f"{result.pending_stability}개{auto_text}{problem_text}"
        )
        if result.pending_stability and self._schedule_followup:
            self.status_label.setText(self.status_label.text() + " · 잠시 후 한 번 더 확인합니다.")
            QTimer.singleShot(1500, lambda: self.scan_now(False))
        if result.auto_organized:
            self.papers_auto_organized.emit(list(result.auto_organized))
            self.library_changed.emit()
        self.queue_changed.emit()

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
        self.form.set_metadata(
            self._controller.suggest_metadata(item) if item else None
        )
        enabled = item is not None
        self.open_button.setEnabled(enabled)
        self.organize_button.setEnabled(enabled)
        self.trash_button.setEnabled(enabled)
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
        if item.detection_status not in {"academic_likely", "patent_likely"} and QMessageBox.question(
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
        message = f"PaperPack 보관 완료: {result.pdf_path}"
        if result.warning:
            message += f"\n경고: {result.warning}"
        QMessageBox.information(self, "논문 정리 완료", message)
        self.library_changed.emit()
        self.queue_changed.emit()
        self.scan_now(False)

    def _trash_selected(self) -> None:
        item = self._selected()
        if item is None:
            return
        if QMessageBox.question(
            self,
            "새 PDF 삭제",
            "파일을 복구 가능한 앱 휴지통으로 옮기고 파일 ID를 보관해 다시 감지되지 "
            "않도록 합니다. 계속할까요?",
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
        self.queue_changed.emit()
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
        self.queue_changed.emit()
        self.scan_now(True)

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()


class _SortableQueueItem(QTableWidgetItem):
    """정렬 키(UserRole+1)가 있으면 그 값으로, 없으면 표시 텍스트로 정렬한다."""

    def __lt__(self, other) -> bool:
        left = self.data(Qt.UserRole + 1)
        right = other.data(Qt.UserRole + 1)
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


class AnalysisQueueWidget(QWidget):
    summary_requested = pyqtSignal(str)
    library_requested = pyqtSignal(str)
    analysis_progress = pyqtSignal(str, bool)

    def __init__(
        self,
        controller: LibraryWorkflowController,
        background_analysis: BackgroundAnalysisService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._background_analysis = background_analysis
        self._analysis_worker: _BackgroundAnalysisWorker | None = None
        self._items: list[AnalysisQueueItem] = []
        self._analysis_running = False
        self._current_analysis_title = ""
        root = QVBoxLayout(self)
        note = QLabel(
            "새로 발견된 PDF가 재시작 후에도 유지되는 분석 대기열입니다. "
            "AI가 준비되지 않았으면 자동 호출하지 않고 여기에서 기다립니다."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["우선순위", "상태", "제목", "실패 사유", "파일"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(self._open_completed_in_library)
        root.addWidget(self.table, 1)
        actions = QHBoxLayout()
        refresh_button = QPushButton("새로고침")
        select_all_button = QPushButton("전체 선택")
        self.priority_button = QPushButton("최우선으로 표시")
        self.summary_button = QPushButton("즉시 요약으로 보내기")
        self.remove_button = QPushButton("큐에서만 제거")
        self.retry_button = QPushButton("실패 항목 다시 분석")
        self.run_now_button = QPushButton("선택 항목 지금 분석")
        self.background_button = QPushButton("백그라운드 분석 시작")
        refresh_button.clicked.connect(self.refresh)
        select_all_button.clicked.connect(self.table.selectAll)
        self.priority_button.clicked.connect(self._toggle_priority)
        self.summary_button.clicked.connect(self._send_to_summary)
        self.remove_button.clicked.connect(self._remove_selected)
        self.retry_button.clicked.connect(self._retry_selected)
        self.run_now_button.clicked.connect(self._run_selected_now)
        self.background_button.clicked.connect(self._toggle_background)
        actions.addWidget(refresh_button)
        actions.addWidget(select_all_button)
        actions.addWidget(self.priority_button)
        actions.addWidget(self.summary_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.run_now_button)
        actions.addWidget(self.background_button)
        actions.addStretch(1)
        root.addLayout(actions)
        self.status_label = QLabel()
        root.addWidget(self.status_label)
        self._selection_changed()
        self.refresh()
        if (
            self._background_analysis is not None
            and self._controller.settings().background_analysis_enabled
        ):
            QTimer.singleShot(0, self.start_background_analysis)

    def refresh(self) -> None:
        try:
            self._items = self._controller.analysis_queue()
        except Exception as exc:
            self._items = []
            self.status_label.setText(f"분석 큐 읽기 실패: {exc}")
        status_labels = {
            "pending_review": "검토 대기",
            "organized_pending_analysis": "정리됨 · 분석 대기",
            "analyzing": "분석 중",
            "completed": "완료",
            "failed": "실패",
        }
        selected_id = self._selected_queue_id()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            values = [
                "높음" if item.priority else "보통",
                status_labels.get(item.status, item.status),
                item.title,
                item.last_error if item.status == "failed" else "",
                item.path,
            ]
            sort_keys = [1 - int(bool(item.priority)), None, None, None, None]
            analyzing = item.status == "analyzing"
            for column, value in enumerate(values):
                cell = _SortableQueueItem(value)
                cell.setData(Qt.UserRole, item.queue_id)
                if sort_keys[column] is not None:
                    cell.setData(Qt.UserRole + 1, sort_keys[column])
                if analyzing:
                    cell.setBackground(QColor(255, 243, 205))
                self.table.setItem(row, column, cell)
        self.table.setSortingEnabled(True)
        if selected_id:
            self._reselect(selected_id)
        self.status_label.setText(f"분석 큐 {len(self._items)}개")
        self._update_background_button()
        self._selection_changed()
        self._emit_progress()

    def _emit_progress(self) -> None:
        waiting = sum(
            1 for item in self._items if item.status == "organized_pending_analysis"
        )
        analyzing = sum(1 for item in self._items if item.status == "analyzing")
        done = sum(1 for item in self._items if item.status == "completed")
        failed = sum(1 for item in self._items if item.status == "failed")
        busy = self._analysis_running or analyzing > 0
        counts = f"대기 {waiting} · 완료 {done} · 실패 {failed}"
        if busy and self._current_analysis_title:
            message = f"분석 중: {self._current_analysis_title} ({counts})"
        elif busy:
            message = f"분석 중 ({counts})"
        else:
            message = f"분석 큐 {counts}"
        self.analysis_progress.emit(message, busy)

    def _selected_queue_id(self) -> str | None:
        row = self.table.currentRow()
        cell = self.table.item(row, 0) if row >= 0 else None
        return cell.data(Qt.UserRole) if cell is not None else None

    def _reselect(self, queue_id: str) -> None:
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell is not None and cell.data(Qt.UserRole) == queue_id:
                self.table.selectRow(row)
                return

    def _selected(self) -> AnalysisQueueItem | None:
        queue_id = self._selected_queue_id()
        if queue_id is None:
            return None
        return next(
            (item for item in self._items if item.queue_id == queue_id), None
        )

    def _selection_changed(self) -> None:
        item = self._selected()
        enabled = item is not None
        mutable = bool(item and item.status != "analyzing")
        self.priority_button.setEnabled(mutable)
        self.summary_button.setEnabled(bool(item and Path(item.path).is_file()))
        self.remove_button.setEnabled(mutable)
        self.retry_button.setEnabled(
            any(item.status == "failed" for item in self._selected_items())
        )
        self.run_now_button.setEnabled(
            bool(
                item
                and item.status
                in {"organized_pending_analysis", "failed", "completed"}
                and Path(item.path).is_file()
                and self._background_analysis is not None
            )
        )
        if item:
            self.priority_button.setText(
                "보통 우선순위로 변경" if item.priority else "최우선으로 표시"
            )

    def _selected_items(self) -> list[AnalysisQueueItem]:
        queue_ids = {
            cell.data(Qt.UserRole)
            for cell in self.table.selectedItems()
            if cell.column() == 0
        }
        return [item for item in self._items if item.queue_id in queue_ids]

    def _retry_selected(self) -> None:
        failed = [item for item in self._selected_items() if item.status == "failed"]
        if not failed:
            return
        try:
            for item in failed:
                self._controller.retry_queue_item(item.queue_id, high=True)
        except Exception as exc:
            QMessageBox.warning(self, "재분석 요청 실패", str(exc))
            return
        self.start_background_analysis()
        if self._analysis_worker is not None:
            self._analysis_worker.request_wake()
        self.refresh()

    def _toggle_priority(self) -> None:
        item = self._selected()
        if item is None:
            return
        try:
            self._controller.set_queue_priority(item.queue_id, not bool(item.priority))
        except Exception as exc:
            QMessageBox.warning(self, "우선순위 변경 실패", str(exc))
            return
        self.refresh()

    def _send_to_summary(self) -> None:
        item = self._selected()
        if item is None:
            return
        if not Path(item.path).is_file():
            QMessageBox.warning(self, "파일 없음", "큐에 기록된 PDF를 찾을 수 없습니다.")
            return
        try:
            pdf = self._controller.materialize_pdf(Path(item.path))
        except Exception as exc:
            QMessageBox.warning(self, "PDF 준비 실패", str(exc))
            return
        self.summary_requested.emit(str(pdf))

    def _open_completed_in_library(self, _row: int, _column: int) -> None:
        item = self._selected()
        if item is not None and item.status == "completed":
            self.library_requested.emit(item.path)

    def _remove_selected(self) -> None:
        item = self._selected()
        if item is None:
            return
        if QMessageBox.question(
            self,
            "큐 항목 제거",
            "분석 큐 기록만 제거합니다. PDF와 paperpack은 삭제되지 않습니다. 계속할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            self._controller.remove_from_queue(item.queue_id)
        except Exception as exc:
            QMessageBox.warning(self, "큐 제거 실패", str(exc))
            return
        self.refresh()

    def _run_selected_now(self) -> None:
        item = self._selected()
        if item is None or self._background_analysis is None:
            return
        try:
            if item.status in {"failed", "completed"}:
                self._controller.retry_queue_item(item.queue_id, high=True)
            elif item.status == "organized_pending_analysis":
                self._controller.set_queue_priority(item.queue_id, True)
            else:
                return
            self._controller.set_background_analysis_enabled(True)
        except Exception as exc:
            QMessageBox.warning(self, "수동 분석 요청 실패", str(exc))
            return
        self.start_background_analysis()
        if self._analysis_worker is not None:
            self._analysis_worker.request_wake()
        self.status_label.setText(f"최우선 분석 요청: {item.title}")
        self.refresh()

    def _toggle_background(self) -> None:
        if self._analysis_worker is not None and self._analysis_worker.isRunning():
            self.stop_background_analysis(persist=True)
        else:
            try:
                self._controller.set_background_analysis_enabled(True)
            except Exception as exc:
                QMessageBox.warning(self, "백그라운드 설정 실패", str(exc))
                return
            self.start_background_analysis()

    def start_background_analysis(self) -> None:
        if self._background_analysis is None:
            self.status_label.setText("백그라운드 분석 서비스가 연결되지 않았습니다.")
            return
        if self._analysis_worker is not None and self._analysis_worker.isRunning():
            self._analysis_worker.request_wake()
            return
        worker = _BackgroundAnalysisWorker(self._background_analysis, self)
        worker.event.connect(self._analysis_event)
        worker.queue_changed.connect(self.refresh)
        worker.finished.connect(self._analysis_worker_finished)
        self._analysis_worker = worker
        self.status_label.setText("백그라운드 분석을 시작합니다…")
        worker.start()
        self._update_background_button()

    def stop_background_analysis(self, *, persist: bool) -> None:
        if persist:
            try:
                self._controller.set_background_analysis_enabled(False)
            except Exception as exc:
                QMessageBox.warning(self, "백그라운드 설정 실패", str(exc))
                return
        if self._analysis_worker is not None:
            self._analysis_worker.request_stop()
            self.status_label.setText(
                "백그라운드 중지 요청됨 · 진행 중인 논문이 끝난 뒤 멈춥니다."
            )
        self._update_background_button()

    def _analysis_event(self, event: AnalysisRunEvent) -> None:
        labels = {
            "started": "분석 중",
            "idle": "대기",
            "waiting": "AI 준비 대기",
            "completed": "완료",
            "failed": "실패",
            "disabled": "중지",
        }
        if event.state == "started":
            self._analysis_running = True
            self._current_analysis_title = event.title
        else:
            self._analysis_running = False
            self._current_analysis_title = ""
        self.status_label.setText(f"{labels.get(event.state, event.state)} · {event.message}")
        self._emit_progress()

    def _analysis_worker_finished(self) -> None:
        worker = self._analysis_worker
        self._analysis_worker = None
        if worker is not None:
            worker.deleteLater()
        self._update_background_button()

    def _update_background_button(self) -> None:
        running = bool(
            self._analysis_worker is not None and self._analysis_worker.isRunning()
        )
        self.background_button.setText(
            "백그라운드 분석 중지" if running else "백그라운드 분석 시작"
        )
        self.background_button.setEnabled(self._background_analysis is not None)

    def is_analysis_busy(self) -> bool:
        return bool(
            self._analysis_worker is not None
            and self._analysis_worker.isRunning()
            and self._analysis_worker.is_processing()
        )

    def is_background_running(self) -> bool:
        return bool(
            self._analysis_worker is not None and self._analysis_worker.isRunning()
        )

    def pause_background_analysis(self) -> bool:
        """Stop an idle worker before foreground AI work; never create overlap."""

        worker = self._analysis_worker
        if worker is None or not worker.isRunning():
            return True
        if worker.is_processing():
            worker.request_stop()
            return False
        worker.request_stop()
        return worker.wait(3000)

    def shutdown_background_analysis(self) -> None:
        worker = self._analysis_worker
        if worker is None:
            return
        worker.request_stop()
        worker.wait(2000)


class LibraryWidget(QWidget):
    metadata_changed = pyqtSignal()

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._entries: list[LibraryEntry] = []
        root = QVBoxLayout(self)
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "제목·저자·분야·태그와 논문 본문 전체에서 검색"
        )
        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(lambda: self.refresh(True))
        self.search_edit.returnPressed.connect(self.refresh)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(refresh_button)
        root.addLayout(search_row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["제목", "저널/학회", "저자", "연도", "분야", "분석 상태"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._open_selected())

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.form = MetadataForm("선택한 논문의 PaperPack 색인 편집")
        self.form.set_metadata(None)
        detail_layout.addWidget(self.form)
        analysis_group = QGroupBox("AI 분석 내용")
        analysis_layout = QVBoxLayout(analysis_group)
        self.analysis_view = QTextBrowser()
        self.analysis_view.setOpenExternalLinks(False)
        analysis_layout.addWidget(self.analysis_view)
        detail_layout.addWidget(analysis_group, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.table)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)
        self._render_analysis(None)
        actions = QHBoxLayout()
        self.save_button = QPushButton("수정 저장 및 재색인")
        self.open_button = QPushButton("sPDF로 열기")
        self.apply_pdf_button = QPushButton("편집본을 PaperPack에 적용")
        self.discard_pdf_button = QPushButton("편집본 폐기")
        self.save_button.clicked.connect(self._save_selected)
        self.open_button.clicked.connect(self._open_selected)
        self.apply_pdf_button.clicked.connect(self._apply_pdf_edit)
        self.discard_pdf_button.clicked.connect(self._discard_pdf_edit)
        self.save_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.apply_pdf_button.setEnabled(False)
        self.discard_pdf_button.setEnabled(False)
        actions.addWidget(self.save_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.apply_pdf_button)
        actions.addWidget(self.discard_pdf_button)
        actions.addStretch(1)
        root.addLayout(actions)
        self.status_label = QLabel()
        root.addWidget(self.status_label)
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        if force:
            self._controller.invalidate_library_cache()
        query = self.search_edit.text().strip()
        try:
            self._entries = (
                self._controller.search_library(query)
                if query
                else self._controller.list_library()
            )
        except Exception as exc:
            self.status_label.setText(f"라이브러리 읽기 실패: {exc}")
            return
        self.table.setRowCount(len(self._entries))
        queue_by_path = {
            str(Path(item.path).resolve()): item
            for item in self._controller.analysis_queue()
        }
        status_labels = {
            "pending_review": "검토 대기",
            "organized_pending_analysis": "분석 대기",
            "analyzing": "분석 중",
            "completed": "분석 완료",
            "failed": "분석 실패",
        }
        for row, entry in enumerate(self._entries):
            metadata = entry.metadata
            queue_item = queue_by_path.get(str(entry.sidecar_path.resolve()))
            values = [
                metadata.title,
                metadata.venue,
                ", ".join(metadata.authors),
                str(metadata.year or ""),
                f"{metadata.category} / {metadata.subcategory}",
                status_labels.get(queue_item.status, "미등록") if queue_item else "미등록",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.form.set_metadata(None)
        self._render_analysis(None)
        self.save_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.apply_pdf_button.setEnabled(False)
        self.discard_pdf_button.setEnabled(False)
        self.status_label.setText(f"논문 파일 {len(self._entries)}개")

    def _selected(self) -> LibraryEntry | None:
        row = self.table.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def select_path(self, path: str | Path) -> bool:
        target = Path(path).expanduser().resolve()
        self.refresh(True)
        for row, entry in enumerate(self._entries):
            if entry.sidecar_path.resolve() == target:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                return True
        return False

    def _selection_changed(self) -> None:
        entry = self._selected()
        self.form.set_metadata(entry.metadata if entry else None)
        self.save_button.setEnabled(entry is not None)
        self.open_button.setEnabled(bool(entry and entry.pdf_path.is_file()))
        self._render_analysis(entry)
        self._refresh_pdf_edit_actions(entry)

    def _render_analysis(self, entry: LibraryEntry | None) -> None:
        """선택 논문의 description/analysis 내용을 읽기 전용으로 보여준다."""
        if entry is None:
            self.analysis_view.setHtml(
                "<p style='color:#777'>왼쪽 목록에서 논문을 선택하면 "
                "AI 분석 내용이 표시됩니다.</p>"
            )
            return
        description = entry.record.get("description", {})
        analysis = entry.record.get("analysis", {})
        esc = lambda value: html.escape(str(value or ""))
        bullets = lambda values: (
            "<ul>" + "".join(f"<li>{esc(item)}</li>" for item in values) + "</ul>"
            if values
            else "<p style='color:#999'>없음</p>"
        )
        sections: list[str] = []
        summary = description.get("summary_ko") or ""
        if not summary and not analysis:
            self.analysis_view.setHtml(
                "<p style='color:#777'>아직 AI 분석 결과가 없습니다. "
                "분석 큐에서 백그라운드 분석이 끝나면 이곳에 표시됩니다.</p>"
            )
            return
        if summary:
            sections.append(f"<h3>요약</h3><p>{esc(summary)}</p>")
        question = description.get("research_question") or ""
        if question:
            sections.append(f"<h3>연구 질문</h3><p>{esc(question)}</p>")
        for label, key in (
            ("방법", "methods"),
            ("핵심 기여", "contributions"),
            ("한계", "limitations"),
            ("키워드", "keywords"),
        ):
            values = [str(item) for item in description.get(key) or []]
            if values:
                sections.append(f"<h3>{label}</h3>{bullets(values)}")
        provenance = analysis.get("provenance") or entry.record.get(
            "provenance", {}
        ).get("summary")
        if isinstance(provenance, dict) and provenance.get("provider"):
            sections.append(
                "<p style='color:#777'>"
                f"{esc(provenance.get('provider'))} / {esc(provenance.get('model'))}"
                f" · {esc(analysis.get('completed_at', ''))}</p>"
            )
        self.analysis_view.setHtml("".join(sections))

    def _refresh_pdf_edit_actions(self, entry: LibraryEntry | None) -> None:
        is_pack = bool(entry and entry.pdf_path.suffix.casefold() == ".paperpack")
        self.apply_pdf_button.setEnabled(is_pack)
        if not is_pack:
            self.discard_pdf_button.setEnabled(False)
            return
        try:
            self.discard_pdf_button.setEnabled(
                self._controller.paperpack_working_copy(entry.pdf_path) is not None
            )
        except Exception:
            self.discard_pdf_button.setEnabled(True)

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
        status = "PaperPack 메타데이터 저장 및 통합 인덱스 재생성을 완료했습니다."
        self.refresh()
        self.status_label.setText(status)
        self.metadata_changed.emit()

    def _open_selected(self) -> None:
        entry = self._selected()
        if entry:
            try:
                editable_pdf = self._controller.materialize_editable_pdf(entry.pdf_path)
                open_pdf(editable_pdf, self)
                self._refresh_pdf_edit_actions(entry)
            except Exception as exc:
                QMessageBox.warning(self, "sPDF 열기 실패", str(exc))

    def _apply_pdf_edit(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        if QMessageBox.question(
            self,
            "PaperPack에 편집본 적용",
            "sPDF에서 먼저 저장한 변경만 적용됩니다. 저장된 편집본으로 "
            "PaperPack의 PDF를 교체할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            result = self._controller.apply_paperpack_working_copy(entry.pdf_path)
        except Exception as exc:
            QMessageBox.warning(self, "편집본 적용 실패", str(exc))
            return
        message = f"편집된 PDF를 PaperPack 리비전 {result.revision}로 저장했습니다."
        if result.warning:
            message += f" 경고: {result.warning}"
        self.refresh(True)
        self.status_label.setText(message)
        self.metadata_changed.emit()

    def _discard_pdf_edit(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        if QMessageBox.question(
            self,
            "편집본 폐기",
            "PaperPack 원본은 유지하고 sPDF 작업 복사본만 삭제할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            removed = self._controller.discard_paperpack_working_copy(entry.pdf_path)
        except Exception as exc:
            QMessageBox.warning(self, "편집본 폐기 실패", str(exc))
            return
        self._refresh_pdf_edit_actions(entry)
        self.status_label.setText(
            "sPDF 편집본을 폐기했습니다." if removed else "폐기할 편집본이 없습니다."
        )
