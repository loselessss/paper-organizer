"""Collection review and editable JSON library widgets."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from threading import Event

from PyQt5.QtCore import QMimeData, QProcess, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QDrag
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QSizePolicy,
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
    TrashEntry,
)
from paper_organizer.application.analysis_queue import AnalysisQueueItem
from paper_organizer.application.background_analysis import (
    AnalysisRunEvent,
    BackgroundAnalysisService,
)
from paper_organizer.application.conversational_search import requires_ai_search
from paper_organizer.application.library_translation import (
    LibraryTranslation,
    LibraryTranslationService,
    analysis_translation_source_hash,
)
from paper_organizer.integrations.spdf_bridge import open_pdf


_REVIEW_DRAG_MIME = "application/x-paper-organizer-review-items"
_CLAIM_BOUNDARY_RE = re.compile(
    r"^(?:"
    r"(?:【|\[)?\s*청구항\s*\d+\s*(?:】|\])?"
    r"|제\s*\d+\s*항"
    r"|claims?\s*:?"
    r"|\d+\s*[.)]"
    r")",
    re.IGNORECASE,
)
from paper_organizer.core.patent import (
    looks_like_registration_number,
    preferred_patent_number,
)


def _format_claims_for_display(value: str) -> str:
    """Join extraction-only soft wraps while retaining claim boundaries."""

    paragraphs: list[str] = []
    current = ""
    for raw_line in str(value or "").replace("\r\n", "\n").splitlines():
        line = " ".join(raw_line.split())
        if not line:
            if current:
                paragraphs.append(current)
                current = ""
            continue
        if _CLAIM_BOUNDARY_RE.match(line):
            if current:
                paragraphs.append(current)
            current = line
        elif current:
            current = f"{current} {line}"
        else:
            current = line
    if current:
        paragraphs.append(current)
    return "\n\n".join(paragraphs)


def _has_previous_analysis_translation(record: dict) -> bool:
    translations = record.get("translations")
    translations = translations if isinstance(translations, dict) else {}
    analysis = translations.get("analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    previous = analysis.get("previous_ko")
    return bool(
        isinstance(previous, dict)
        and str(previous.get("text") or "").strip()
    )


def _analysis_version_label(record: dict) -> str:
    analysis = record.get("analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    provenance = analysis.get("provenance")
    if not isinstance(provenance, dict):
        root_provenance = record.get("provenance")
        provenance = (
            root_provenance.get("summary")
            if isinstance(root_provenance, dict)
            else {}
        )
    if not isinstance(provenance, dict):
        return ""
    app_version = str(provenance.get("app_version") or "").strip()
    if app_version:
        return f"v{app_version.removeprefix('v')}"
    prompt_version = str(provenance.get("prompt_version") or "")
    marker = next(
        (
            value
            for value in ("paper-summary-v", "patent-summary-v")
            if value in prompt_version
        ),
        "",
    )
    if not marker:
        return ""
    suffix = prompt_version.split(marker, 1)[1]
    number = suffix.split("-", 1)[0]
    return f"v{number}" if number.isdigit() else ""


def _format_library_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        return text


def _analysis_failed(record: dict) -> bool:
    workflow = record.get("workflow")
    workflow = workflow if isinstance(workflow, dict) else {}
    analysis = record.get("analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    last_attempt = analysis.get("last_attempt")
    return bool(
        workflow.get("analysis_status") == "failed"
        or analysis.get("status") == "failed"
        or (
            isinstance(last_attempt, dict)
            and last_attempt.get("status") == "failed"
        )
    )


class _ReviewQueueTable(QTableWidget):
    """Drag selected review rows to the analysis queue by stable file ID."""

    def startDrag(self, _supported_actions) -> None:
        file_ids = []
        for row in sorted({index.row() for index in self.selectedIndexes()}):
            cell = self.item(row, 0)
            file_id = cell.data(Qt.UserRole) if cell is not None else None
            if file_id:
                file_ids.append(str(file_id))
        if not file_ids:
            return
        mime = QMimeData()
        mime.setData(
            _REVIEW_DRAG_MIME,
            json.dumps(file_ids).encode("utf-8"),
        )
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec_(Qt.CopyAction)


class _AnalysisQueueDropTable(QTableWidget):
    """Accept review rows as an explicit request to store and analyze them."""

    review_items_dropped = pyqtSignal(list)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(_REVIEW_DRAG_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(_REVIEW_DRAG_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat(_REVIEW_DRAG_MIME):
            super().dropEvent(event)
            return
        try:
            file_ids = json.loads(
                bytes(event.mimeData().data(_REVIEW_DRAG_MIME)).decode("utf-8")
            )
            if not isinstance(file_ids, list) or not all(
                isinstance(value, str) and value for value in file_ids
            ):
                raise ValueError
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            event.ignore()
            return
        self.review_items_dropped.emit(list(dict.fromkeys(file_ids)))
        event.acceptProposedAction()


_DETECTION_LABELS = {
    "academic_likely": "학술 논문",
    "patent_likely": "특허",
    "needs_ocr": "OCR 필요",
    "needs_review": "검토 필요",
}

_DUPLICATE_KIND_LABELS = {
    "exact_file": "동일 파일",
    "same_work": "같은 문헌",
    "possible_related": "중복 후보",
    "different": "다른 문헌",
}


def _trash_judgment(entry: TrashEntry) -> str:
    if entry.detection_status:
        return _DETECTION_LABELS.get(
            entry.detection_status, entry.detection_status
        )
    if entry.kind == "unorganized_duplicate":
        return "중복 파일"
    if entry.kind == "discarded_new_pdf":
        return "제외됨"
    return "기록 없음"


def _trash_duplicate(entry: TrashEntry) -> str:
    title = entry.duplicate_title
    has_duplicate_path = str(entry.duplicate_of) not in {"", "."}
    if not title and has_duplicate_path:
        title = entry.duplicate_of.stem
    if not title:
        return "없음"
    kind = _DUPLICATE_KIND_LABELS.get(entry.duplicate_kind, entry.duplicate_kind)
    details = [title]
    if kind:
        details.append(kind)
    if entry.duplicate_score is not None:
        details.append(f"{entry.duplicate_score:.2f}")
    return " · ".join(details)


class TrashRestoreDialog(QDialog):
    """Show recoverable excluded PDFs in a spacious, multi-select table."""

    def __init__(self, entries: list[TrashEntry], parent=None) -> None:
        super().__init__(parent)
        self._entries = entries
        self.setWindowTitle("제외 파일 복원")
        self.setMinimumSize(900, 460)
        self.resize(1080, 560)

        layout = QVBoxLayout(self)
        description = QLabel(
            "복원할 파일을 선택하세요. Ctrl 또는 Shift를 누르면 여러 파일을 "
            "한 번에 선택할 수 있습니다."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.table = QTableWidget(len(entries), 4)
        self.table.setHorizontalHeaderLabels(
            ["파일", "판정", "중복", "추정 제목"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        for row, entry in enumerate(entries):
            values = [
                entry.original_path.name,
                _trash_judgment(entry),
                _trash_duplicate(entry),
                entry.estimated_title or entry.original_path.stem,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setToolTip(str(entry.original_path))
                elif column == 1 and entry.detection_reason:
                    cell.setToolTip(entry.detection_reason)
                elif column == 2 and str(entry.duplicate_of) not in {"", "."}:
                    cell.setToolTip(str(entry.duplicate_of))
                self.table.setItem(row, column, cell)
        if entries:
            self.table.selectRow(0)
        self.table.itemSelectionChanged.connect(self._update_restore_button)
        self.table.cellDoubleClicked.connect(
            lambda _row, _column: self._accept_selection()
        )
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self.restore_button = buttons.button(QDialogButtonBox.Ok)
        self.restore_button.setText("선택 파일 복원")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_restore_button()

    def selected_entries(self) -> list[TrashEntry]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._entries[row] for row in rows]

    def _update_restore_button(self) -> None:
        self.restore_button.setEnabled(bool(self.selected_entries()))

    def _accept_selection(self) -> None:
        if self.selected_entries():
            self.accept()


class _ScanWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller

    def run(self) -> None:
        try:
            self.completed.emit(self._controller.scan(progress=self.progress.emit))
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
        self._immediate_remaining = 0

    def request_stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_cancel(self) -> None:
        self._stop.set()
        self._wake.set()
        cancel = getattr(self._service, "request_cancel", None)
        if cancel is not None:
            cancel()

    def request_wake(self) -> None:
        self._wake.set()

    def request_immediate(self, count: int) -> None:
        """Process explicitly requested items back-to-back without eco waits."""

        self._immediate_remaining += max(0, int(count))
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
            immediate_this_run = self._immediate_remaining > 0
            reset_cancel = getattr(self._service, "reset_cancel", None)
            if reset_cancel is not None:
                reset_cancel()
            self._processing = True
            result = self._service.run_next(
                force=immediate_this_run,
                keep_runtime=lambda: self._immediate_remaining
                > (1 if immediate_this_run else 0),
                on_start=self._notify_started,
                on_progress=self.event.emit,
            )
            self._processing = False
            self.event.emit(result)
            if result.state in {
                "completed",
                "translation_completed",
                "cancelled",
                "failed",
                "ocr_completed",
            }:
                self.queue_changed.emit()
            if result.state == "disabled":
                break
            if (
                result.state
                in {"completed", "translation_completed", "cancelled", "failed"}
                and immediate_this_run
                and self._immediate_remaining
            ):
                self._immediate_remaining -= 1
            if (
                result.state
                in {"completed", "translation_completed", "cancelled", "failed"}
                and self._immediate_remaining
            ):
                continue
            if result.state == "ocr_completed":
                # OCR is only a preparation stage for the same item.
                if self._immediate_remaining:
                    continue
                self._wake.set()
            wait_seconds = (
                1
                if self._immediate_remaining
                else self._service.poll_interval()
            )
            self._wake.wait(wait_seconds)
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
        self.patent_office_edit = QLineEdit()
        self.publication_number_edit = QLineEdit()
        self.application_number_edit = QLineEdit()
        self.assignee_edit = QLineEdit()
        self.category_edit = QLineEdit("Uncategorized")
        self.subcategory_edit = QLineEdit("General")
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("쉼표로 구분")
        self._summary = ""
        self._document_type = "paper"
        self._publication_number = ""
        self._application_number = ""
        self.authors_label = QLabel("저자")
        self.venue_label = QLabel("저널/학회")
        self.patent_office_label = QLabel("특허청")
        self.publication_number_label = QLabel("출원/등록번호")
        self.application_number_label = QLabel("출원번호")
        self.assignee_label = QLabel("출원인/권리자")
        form.addRow("제목", self.title_edit)
        form.addRow(self.authors_label, self.authors_edit)
        form.addRow("연도", self.year_edit)
        form.addRow(self.venue_label, self.venue_edit)
        form.addRow(self.patent_office_label, self.patent_office_edit)
        form.addRow(self.publication_number_label, self.publication_number_edit)
        form.addRow(self.application_number_label, self.application_number_edit)
        form.addRow(self.assignee_label, self.assignee_edit)
        form.addRow("분야", self.category_edit)
        form.addRow("세부분야", self.subcategory_edit)
        form.addRow("태그", self.tags_edit)

    def set_metadata(self, metadata: EditablePaperMetadata | None) -> None:
        value = metadata or EditablePaperMetadata()
        self.title_edit.setText(value.title)
        self.authors_edit.setText(", ".join(value.authors))
        self.year_edit.setText(str(value.year or ""))
        self.venue_edit.setText(value.venue)
        self.patent_office_edit.setText(value.patent_office)
        self._publication_number = value.publication_number
        self._application_number = value.application_number
        self.publication_number_edit.setText(
            preferred_patent_number(
                value.publication_number,
                value.application_number,
            )
        )
        self.application_number_edit.setText(value.application_number)
        self.assignee_edit.setText(value.assignee)
        self.category_edit.setText(value.category)
        self.subcategory_edit.setText(value.subcategory)
        self.tags_edit.setText(", ".join(value.tags))
        self._summary = value.summary
        self._set_document_type(value.document_type)
        self.setEnabled(metadata is not None)

    def _set_document_type(self, document_type: str) -> None:
        self._document_type = "patent" if document_type == "patent" else "paper"
        patent = self._document_type == "patent"
        self.authors_label.setText("발명자" if patent else "저자")
        self.venue_label.setVisible(not patent)
        self.venue_edit.setVisible(not patent)
        for label, editor in (
            (self.patent_office_label, self.patent_office_edit),
            (self.publication_number_label, self.publication_number_edit),
            (self.assignee_label, self.assignee_edit),
        ):
            label.setVisible(patent)
            editor.setVisible(patent)
        self.application_number_label.setVisible(False)
        self.application_number_edit.setVisible(False)

    def metadata(self) -> EditablePaperMetadata:
        year_text = self.year_edit.text().strip()
        if year_text and not year_text.isdigit():
            raise ValueError("연도는 숫자로 입력하세요.")
        split_values = lambda text: [value.strip() for value in text.split(",") if value.strip()]
        patent_number = self.publication_number_edit.text().strip()
        publication_number = self._publication_number
        application_number = self._application_number
        if self._document_type == "patent":
            if looks_like_registration_number(publication_number):
                publication_number = patent_number
            elif application_number:
                application_number = patent_number
            elif publication_number:
                publication_number = patent_number
            else:
                application_number = patent_number
        return EditablePaperMetadata(
            title=self.title_edit.text().strip(),
            authors=split_values(self.authors_edit.text()),
            year=int(year_text) if year_text else None,
            venue=self.venue_edit.text().strip(),
            document_type=self._document_type,
            patent_office=self.patent_office_edit.text().strip(),
            publication_number=publication_number,
            application_number=application_number,
            assignee=self.assignee_edit.text().strip(),
            category=self.category_edit.text().strip() or "Uncategorized",
            subcategory=self.subcategory_edit.text().strip() or "General",
            tags=split_values(self.tags_edit.text()),
            summary=self._summary,
        )


class CollectionReviewWidget(QWidget):
    library_changed = pyqtSignal()
    queue_changed = pyqtSignal()
    papers_auto_organized = pyqtSignal(list)
    immediate_analysis_requested = pyqtSignal(int)
    library_requested = pyqtSignal(str)

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
        self.settings_button = QPushButton("요약 감시 옵션…")
        self.settings_button.clicked.connect(self._show_folder_settings)
        self.status_label = QLabel()
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_label.setWordWrap(True)
        action_row.addWidget(self.scan_button)
        action_row.addWidget(self.settings_button)
        action_row.addWidget(self.status_label, 1)
        root.addLayout(action_row)

        self.table = _ReviewQueueTable(0, 4)
        self.table.setHorizontalHeaderLabels(["파일", "판정", "중복", "추정 제목"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragOnly)
        self.table.setDefaultDropAction(Qt.CopyAction)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(
            lambda row, _column: self._open_row(row)
        )
        root.addWidget(self.table, 1)

        self.detail_label = QLabel("검토할 PDF를 선택하세요.")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumWidth(0)
        self.detail_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        root.addWidget(self.detail_label)
        self.form = MetadataForm("이동 전에 수정할 색인")
        self.form.set_metadata(None)
        root.addWidget(self.form)

        review_actions = QHBoxLayout()
        self.select_all_button = QPushButton("전체 선택")
        self.open_button = QPushButton("sPDF로 열기")
        self.organize_button = QPushButton("선택 항목 분석 큐로 보내기")
        self.trash_button = QPushButton("제외 목록으로 보내기")
        self.restore_button = QPushButton("제외 목록에서 복원…")
        self.select_all_button.clicked.connect(self.table.selectAll)
        self.open_button.clicked.connect(self._open_selected)
        self.organize_button.clicked.connect(self._organize_selected)
        self.trash_button.clicked.connect(self._trash_selected)
        self.restore_button.clicked.connect(self._restore_trash)
        review_actions.addWidget(self.select_all_button)
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
        worker.progress.connect(self.status_label.setText)
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
            values = [
                item.path.name,
                _DETECTION_LABELS.get(item.detection_status, item.detection_status),
                duplicate_text,
                item.metadata.title,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, item.identity.file_sha256)
                self.table.setItem(row, column, cell)
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

    def _selected_items(self) -> list[ReviewItem]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._items[row] for row in rows if 0 <= row < len(self._items)]

    def _selection_changed(self) -> None:
        selected = self._selected_items()
        item = selected[0] if len(selected) == 1 else None
        self.form.set_metadata(
            self._controller.suggest_metadata(item) if item else None
        )
        self.form.setEnabled(item is not None)
        enabled = bool(selected)
        self.open_button.setEnabled(enabled)
        self.organize_button.setEnabled(enabled)
        self.trash_button.setEnabled(enabled)
        if len(selected) > 1:
            self.detail_label.setText(
                f"{len(selected)}개 PDF를 선택했습니다. 일괄 보관할 때는 각 PDF의 "
                "추정 메타데이터를 개별 적용합니다."
            )
            return
        if item is None:
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
        failures: list[str] = []
        for item in self._selected_items():
            try:
                open_pdf(item.path, self)
            except Exception as exc:
                failures.append(f"{item.path.name}: {exc}")
        if failures:
            QMessageBox.warning(
                self, "일부 PDF 열기 실패", "\n".join(failures[:10])
            )

    def _open_row(self, row: int) -> None:
        if not 0 <= row < len(self._items):
            return
        item = self._items[row]
        if (
            item.duplicate is not None
            and item.duplicate.match.kind.value == "exact_file"
        ):
            self.library_requested.emit(str(item.duplicate.sidecar_path))
            return
        try:
            open_pdf(item.path, self)
        except Exception as exc:
            QMessageBox.warning(self, "sPDF 열기 실패", str(exc))

    def _show_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and not self.table.item(index.row(), 0).isSelected():
            self.table.clearSelection()
            self.table.selectRow(index.row())
        items = self._selected_items()
        if not items:
            return
        menu = QMenu(self)
        open_action = menu.addAction("새 PDF를 sPDF로 열기")
        open_action.triggered.connect(self._open_selected)
        if len(items) == 1 and items[0].duplicate is not None:
            duplicate = items[0].duplicate
            existing_action = menu.addAction("기존 라이브러리 분석 보기")
            existing_action.setEnabled(duplicate.sidecar_path.is_file())
            existing_action.triggered.connect(
                lambda: self.library_requested.emit(str(duplicate.sidecar_path))
            )
        menu.addSeparator()
        organize_action = menu.addAction("분석 큐로 보내기")
        organize_action.triggered.connect(self._organize_selected)
        trash_action = menu.addAction("제외 목록으로 보내기")
        trash_action.triggered.connect(self._trash_selected)
        menu.exec_(self.table.viewport().mapToGlobal(position))

    def _organize_selected(self) -> None:
        items = self._selected_items()
        if not items:
            return
        organized = self._organize_items(items, ask_confirmation=True)
        if organized:
            self.immediate_analysis_requested.emit(organized)

    def organize_dropped(self, file_ids: list[str]) -> None:
        if self.is_busy():
            self.status_label.setText(
                "PDF 검색이 끝난 뒤 분석 큐로 다시 끌어다 놓으세요."
            )
            return
        wanted = set(file_ids)
        items = [
            item for item in self._items if item.identity.file_sha256 in wanted
        ]
        if not items:
            self.status_label.setText(
                "드롭한 검토 항목을 찾지 못했습니다. 새 PDF 검색 후 다시 시도하세요."
            )
            return
        organized = self._organize_items(items, ask_confirmation=False)
        if organized:
            self.immediate_analysis_requested.emit(organized)

    def _organize_items(
        self, items: list[ReviewItem], *, ask_confirmation: bool
    ) -> int:
        uncertain = sum(
            item.detection_status not in {"academic_likely", "patent_likely"}
            for item in items
        )
        if len(items) > 1:
            message = (
                f"선택한 {len(items)}개 PDF를 각각의 추정 메타데이터로 보관합니다."
            )
            if uncertain:
                message += f"\n이 중 {uncertain}개는 논문·특허 판정이 불확실합니다."
            message += "\n계속할까요?"
        else:
            message = (
                "학술 논문이나 특허로 확실히 판정되지 않았습니다. "
                "그래도 승인하여 보관할까요?"
            )
        if ask_confirmation and (len(items) > 1 or uncertain) and QMessageBox.question(
            self, "수동 승인 확인", message
        ) != QMessageBox.Yes:
            return 0
        organized = 0
        warnings: list[str] = []
        failures: list[str] = []
        for item in items:
            try:
                metadata = (
                    self.form.metadata()
                    if len(items) == 1
                    else self._controller.suggest_metadata(item)
                )
                result = self._controller.organize(item, metadata)
                organized += 1
                if result.warning:
                    warnings.append(f"{item.path.name}: {result.warning}")
            except Exception as exc:
                failures.append(f"{item.path.name}: {exc}")
        self.status_label.setText(
            f"선택한 PDF {organized}개를 분석 큐로 보냈습니다."
            + (f" · 실패 {len(failures)}개" if failures else "")
        )
        if warnings or failures:
            parts = []
            if warnings:
                parts.append("주의:\n" + "\n".join(warnings[:10]))
            if failures:
                parts.append("실패:\n" + "\n".join(failures[:10]))
            QMessageBox.warning(
                self, "일부 PDF 보관 확인 필요", "\n\n".join(parts)
            )
        if not organized:
            return 0
        self.library_changed.emit()
        self.queue_changed.emit()
        self.scan_now(False)
        return organized

    def _trash_selected(self) -> None:
        items = self._selected_items()
        if not items:
            return
        if QMessageBox.question(
            self,
            "제외 목록으로 보내기",
            f"선택한 파일 {len(items)}개를 복구 가능한 제외 목록으로 옮기고 "
            "파일 ID를 보관해 다시 감지되지 않도록 합니다. 계속할까요?",
        ) != QMessageBox.Yes:
            return
        moved = 0
        failures: list[str] = []
        for item in items:
            try:
                self._controller.trash_confirmed_duplicate(item)
                moved += 1
            except Exception as exc:
                failures.append(f"{item.path.name}: {exc}")
        self.status_label.setText(
            f"선택한 PDF {moved}개를 제외 목록으로 보냈습니다."
            + (f" · 실패 {len(failures)}개" if failures else "")
        )
        if failures:
            QMessageBox.warning(
                self, "일부 제외 실패", "\n".join(failures[:10])
            )
        if not moved:
            return
        self.queue_changed.emit()
        self.scan_now(False)

    def _restore_trash(self) -> None:
        entries = self._controller.list_trash()
        if not entries:
            QMessageBox.information(self, "제외 목록", "복원할 제외 파일이 없습니다.")
            return
        dialog = TrashRestoreDialog(entries, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected = dialog.selected_entries()
        restored: list[Path] = []
        failures: list[str] = []
        for entry in selected:
            try:
                restored.append(self._controller.restore_trash(entry))
            except Exception as exc:
                failures.append(f"{entry.original_path.name}: {exc}")
        if failures:
            QMessageBox.warning(
                self,
                "일부 복원 실패",
                f"{len(restored)}개 복원, {len(failures)}개 실패\n\n"
                + "\n".join(failures),
            )
        elif len(restored) == 1:
            QMessageBox.information(
                self, "복원 완료", f"복원 위치: {restored[0]}"
            )
        else:
            QMessageBox.information(
                self, "복원 완료", f"선택한 파일 {len(restored)}개를 복원했습니다."
            )
        if not restored:
            return
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
    library_requested = pyqtSignal(str)
    review_items_dropped = pyqtSignal(list)
    library_changed = pyqtSignal()
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
        self.table = _AnalysisQueueDropTable(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["우선순위", "상태", "제목", "실패 사유", "파일"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAcceptDrops(True)
        self.table.viewport().setAcceptDrops(True)
        self.table.setDragDropMode(QAbstractItemView.DropOnly)
        self.table.setDropIndicatorShown(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.review_items_dropped.connect(self.review_items_dropped.emit)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(
            lambda _row, _column: self._show_selected_in_library()
        )
        root.addWidget(self.table, 1)
        actions = QHBoxLayout()
        refresh_button = QPushButton("새로고침")
        select_all_button = QPushButton("전체 선택")
        self.priority_button = QPushButton("최우선으로 표시")
        self.run_now_button = QPushButton("선택 항목 바로 분석")
        self.remove_button = QPushButton("선택 항목 큐에서 제외")
        self.retry_button = QPushButton("실패 항목 다시 분석")
        self.background_button = QPushButton("백그라운드 분석 시작")
        self.immediate_stop_button = QPushButton("즉시 정지")
        self.immediate_stop_button.setToolTip(
            "현재 결과를 저장하지 않고 항목을 대기열로 되돌립니다. "
            "앱이 시작한 Ollama 작업은 즉시 종료합니다."
        )
        refresh_button.clicked.connect(self.refresh)
        select_all_button.clicked.connect(self.table.selectAll)
        self.priority_button.clicked.connect(self._toggle_priority)
        self.run_now_button.clicked.connect(self._run_selected_now)
        self.remove_button.clicked.connect(self._remove_selected)
        self.retry_button.clicked.connect(self._retry_selected)
        self.background_button.clicked.connect(self._toggle_background)
        self.immediate_stop_button.clicked.connect(
            self.immediate_stop_background_analysis
        )
        actions.addWidget(refresh_button)
        actions.addWidget(select_all_button)
        actions.addWidget(self.priority_button)
        actions.addWidget(self.run_now_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.background_button)
        actions.addWidget(self.immediate_stop_button)
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
            loaded = self._controller.analysis_queue()
            self._items = [
                item
                for item in loaded
                if item.status not in {"pending_review", "completed"}
            ]
        except Exception as exc:
            self._items = []
            self.status_label.setText(f"분석 큐 읽기 실패: {exc}")
        status_labels = {
            "organized_pending_analysis": "정리됨 · 분석 대기",
            "analyzing": "분석 중",
            "failed": "실패",
        }
        selected_id = self._selected_queue_id()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            status_text = status_labels.get(item.status, item.status)
            if item.task_type == "translation":
                status_text = {
                    "organized_pending_analysis": "AI 번역 대기",
                    "analyzing": "AI 번역 중",
                    "failed": "AI 번역 실패",
                }.get(item.status, status_text)
            values = [
                "높음" if item.priority else "보통",
                status_text,
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
        failed = sum(1 for item in self._items if item.status == "failed")
        busy = self._analysis_running or analyzing > 0
        counts = f"대기 {waiting} · 실패 {failed}"
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
        selected = self._selected_items()
        mutable_items = [value for value in selected if value.status != "analyzing"]
        mutable = bool(mutable_items)
        self.priority_button.setEnabled(mutable)
        self.remove_button.setEnabled(mutable)
        self.retry_button.setEnabled(
            any(item.status == "failed" for item in self._selected_items())
        )
        self.run_now_button.setEnabled(
            bool(
                self._background_analysis is not None
                and any(
                    value.status
                    in {"organized_pending_analysis", "failed"}
                    and Path(value.path).is_file()
                    for value in selected
                )
            )
        )
        if mutable_items:
            all_high = all(value.priority for value in mutable_items)
            self.priority_button.setText(
                "보통 우선순위로 변경" if all_high else "최우선으로 표시"
            )

    def _selected_items(self) -> list[AnalysisQueueItem]:
        queue_ids = {
            cell.data(Qt.UserRole)
            for cell in self.table.selectedItems()
            if cell.column() == 0
        }
        return [item for item in self._items if item.queue_id in queue_ids]

    def _show_selected_in_library(self) -> None:
        item = self._selected()
        if item is None or not Path(item.path).is_file():
            return
        self.library_requested.emit(item.path)

    def _show_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and not self.table.item(index.row(), 0).isSelected():
            self.table.clearSelection()
            self.table.selectRow(index.row())
        items = self._selected_items()
        if not items:
            return
        menu = QMenu(self)
        if len(items) == 1:
            library_action = menu.addAction("라이브러리 분석 내용 보기")
            library_action.setEnabled(Path(items[0].path).is_file())
            library_action.triggered.connect(self._show_selected_in_library)
            menu.addSeparator()
        run_action = menu.addAction("선택 항목 바로 분석")
        run_action.setEnabled(
            any(
                item.status in {"organized_pending_analysis", "failed"}
                and Path(item.path).is_file()
                for item in items
            )
        )
        run_action.triggered.connect(self._run_selected_now)
        retry_action = menu.addAction("실패 항목 다시 분석")
        retry_action.setEnabled(any(item.status == "failed" for item in items))
        retry_action.triggered.connect(self._retry_selected)
        priority_action = menu.addAction(self.priority_button.text())
        priority_action.setEnabled(any(item.status != "analyzing" for item in items))
        priority_action.triggered.connect(self._toggle_priority)
        menu.addSeparator()
        remove_action = menu.addAction("선택 항목 큐에서 제외…")
        remove_action.setEnabled(any(item.status != "analyzing" for item in items))
        remove_action.triggered.connect(self._remove_selected)
        menu.exec_(self.table.viewport().mapToGlobal(position))

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
        items = [
            item for item in self._selected_items() if item.status != "analyzing"
        ]
        if not items:
            return
        high = not all(item.priority for item in items)
        try:
            for item in items:
                self._controller.set_queue_priority(item.queue_id, high)
        except Exception as exc:
            QMessageBox.warning(self, "우선순위 변경 실패", str(exc))
            return
        self.refresh()

    def _remove_selected(self) -> None:
        items = [
            item for item in self._selected_items() if item.status != "analyzing"
        ]
        if not items:
            return
        if QMessageBox.question(
            self,
            "큐 항목 제외",
            f"선택한 {len(items)}건을 분석 큐에서 제외합니다. "
            "PDF와 paperpack은 삭제되지 않습니다. 계속할까요?",
        ) != QMessageBox.Yes:
            return
        failures: list[str] = []
        try:
            for item in items:
                try:
                    self._controller.remove_from_queue(item.queue_id)
                except Exception as exc:
                    failures.append(f"{item.title}: {exc}")
        finally:
            self.refresh()
        if failures:
            QMessageBox.warning(
                self,
                "일부 큐 제외 실패",
                "\n".join(failures[:10]),
            )

    def _run_selected_now(self) -> None:
        items = [
            item
            for item in self._selected_items()
            if item.status in {"organized_pending_analysis", "failed"}
            and Path(item.path).is_file()
        ]
        if not items or self._background_analysis is None:
            return
        try:
            for item in items:
                if item.status == "failed":
                    self._controller.retry_queue_item(item.queue_id, high=True)
                else:
                    self._controller.set_queue_priority(item.queue_id, True)
            self._controller.set_background_analysis_enabled(True)
        except Exception as exc:
            QMessageBox.warning(self, "수동 분석 요청 실패", str(exc))
            return
        self.start_background_analysis(immediate_count=len(items))
        self.status_label.setText(
            f"선택한 {len(items)}건을 최우선 분석 대기열에 넣었습니다."
        )
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

    def start_background_analysis(self, *, immediate_count: int = 0) -> None:
        if self._background_analysis is None:
            self.status_label.setText("백그라운드 분석 서비스가 연결되지 않았습니다.")
            return
        if self._analysis_worker is not None and self._analysis_worker.isRunning():
            if immediate_count:
                self._analysis_worker.request_immediate(immediate_count)
            else:
                self._analysis_worker.request_wake()
            return
        worker = _BackgroundAnalysisWorker(self._background_analysis, self)
        worker.event.connect(self._analysis_event)
        worker.queue_changed.connect(self.refresh)
        worker.finished.connect(self._analysis_worker_finished)
        self._analysis_worker = worker
        if immediate_count:
            worker.request_immediate(immediate_count)
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

    def immediate_stop_background_analysis(self) -> None:
        worker = self._analysis_worker
        if worker is None or not worker.isRunning():
            return
        try:
            self._controller.set_background_analysis_enabled(False)
        except Exception as exc:
            QMessageBox.warning(self, "백그라운드 설정 실패", str(exc))
            return
        worker.request_cancel()
        self.status_label.setText(
            "즉시 정지 요청됨 · 현재 결과를 버리고 항목을 대기열로 되돌립니다."
        )
        self._update_background_button()

    def _analysis_event(self, event: AnalysisRunEvent) -> None:
        labels = {
            "started": "분석 중",
            "translation_started": "AI 번역 중",
            "ocr_started": "OCR 시작",
            "ocr_progress": "OCR 진행",
            "ocr_completed": "OCR 완료",
            "idle": "대기",
            "waiting": "AI 준비 대기",
            "completed": "완료",
            "translation_completed": "AI 번역 완료",
            "cancelled": "즉시 정지",
            "failed": "실패",
            "disabled": "중지",
        }
        if event.state in {
            "started",
            "translation_started",
            "ocr_started",
            "ocr_progress",
        }:
            self._analysis_running = True
            self._current_analysis_title = event.title
        else:
            self._analysis_running = False
            self._current_analysis_title = ""
        self.status_label.setText(f"{labels.get(event.state, event.state)} · {event.message}")
        if event.state in {"completed", "translation_completed"}:
            self.library_changed.emit()
        self._update_background_button()
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
        self.immediate_stop_button.setEnabled(
            bool(
                self._background_analysis is not None
                and running
                and self._analysis_worker is not None
                and self._analysis_worker.is_processing()
            )
        )

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
    reanalysis_queued = pyqtSignal(int)
    translation_queued = pyqtSignal(int)
    natural_search_requested = pyqtSignal(str)

    def __init__(
        self,
        controller: LibraryWorkflowController,
        parent=None,
        *,
        translation_service: LibraryTranslationService | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._translation_service = translation_service
        self._translation_path = ""
        self._translation_cache: dict[str, LibraryTranslation] = {}
        self._entries: list[LibraryEntry] = []
        root = QVBoxLayout(self)
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "제목·저자·키워드 검색 · 자연어 질문 검색도 가능"
        )
        self.search_edit.setClearButtonEnabled(True)
        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(self._search_or_refresh)
        self.search_edit.returnPressed.connect(self._submit_search)
        self.search_edit.textChanged.connect(self._search_text_changed)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(refresh_button)
        root.addLayout(search_row)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "제목",
                "저자/발명자",
                "연도",
                "분야",
                "분석 버전",
                "번역 상태",
                "등록일",
                "분석일",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 72)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.cellDoubleClicked.connect(
            lambda row, _column: self._open_row(row)
        )

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.form = MetadataForm("선택한 논문의 PaperPack 색인 편집")
        self.form.set_metadata(None)
        detail_layout.addWidget(self.form)
        edit_actions = QHBoxLayout()
        self.save_button = QPushButton("색인 편집 저장 및 재색인")
        self.translation_button = QPushButton("AI 번역")
        self.translation_button.setCheckable(True)
        self.translation_button.setToolTip(
            "현재 AI 분석을 한국어로 번역합니다. 원문 색인은 바꾸지 않고 "
            "번역 보기와 원문 보기를 전환합니다."
        )
        self.restore_translation_button = QPushButton("이전 번역 복원")
        self.restore_translation_button.setEnabled(False)
        self.restore_translation_button.setToolTip(
            "PaperPack에 한 건만 보관한 직전 AI 번역으로 되돌립니다."
        )
        self.translation_button.toggled.connect(self._translation_toggled)
        self.restore_translation_button.clicked.connect(
            self._restore_previous_translation
        )
        self.save_button.clicked.connect(self._save_selected)
        self.save_button.setEnabled(False)
        edit_actions.addStretch(1)
        edit_actions.addWidget(self.restore_translation_button)
        edit_actions.addWidget(self.translation_button)
        edit_actions.addWidget(self.save_button)
        detail_layout.addLayout(edit_actions)
        analysis_group = QGroupBox("AI 분석 내용")
        analysis_layout = QVBoxLayout(analysis_group)
        self.analysis_view = QTextBrowser()
        self.analysis_view.setOpenExternalLinks(False)
        self.analysis_view.setLineWrapMode(QTextEdit.WidgetWidth)
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
        self.open_button = QPushButton("sPDF로 열기")
        self.apply_pdf_button = QPushButton("편집본을 PaperPack에 적용")
        self.discard_pdf_button = QPushButton("편집본 폐기")
        self.delete_button = QPushButton("선택 항목을 앱 휴지통으로 이동")
        self.reanalyze_selected_button = QPushButton("선택 논문 재요약")
        self.reanalyze_all_button = QPushButton("전체 논문 재요약")
        self.approve_category_button = QPushButton("추천 연구분야 승인 후 재분석")
        self.open_button.clicked.connect(self._open_selected)
        self.apply_pdf_button.clicked.connect(self._apply_pdf_edit)
        self.discard_pdf_button.clicked.connect(self._discard_pdf_edit)
        self.delete_button.clicked.connect(self._delete_selected)
        self.reanalyze_selected_button.clicked.connect(self._reanalyze_selected)
        self.reanalyze_all_button.clicked.connect(self._reanalyze_all)
        self.approve_category_button.clicked.connect(self._approve_category)
        self.open_button.setEnabled(False)
        self.apply_pdf_button.setEnabled(False)
        self.discard_pdf_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.reanalyze_selected_button.setEnabled(False)
        self.reanalyze_all_button.setEnabled(False)
        self.approve_category_button.setEnabled(False)
        actions.addWidget(self.open_button)
        actions.addWidget(self.apply_pdf_button)
        actions.addWidget(self.discard_pdf_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        root.addLayout(actions)
        analysis_actions = QHBoxLayout()
        analysis_actions.addWidget(self.reanalyze_selected_button)
        analysis_actions.addWidget(self.reanalyze_all_button)
        analysis_actions.addWidget(self.approve_category_button)
        analysis_actions.addStretch(1)
        root.addLayout(analysis_actions)
        self.status_label = QLabel()
        root.addWidget(self.status_label)
        self.refresh()

    def _search_text_changed(self, text: str) -> None:
        if not text.strip():
            self.refresh()

    def _submit_search(self) -> None:
        query = self.search_edit.text().strip()
        if query and requires_ai_search(query):
            self.natural_search_requested.emit(query)
            return
        self.refresh()

    def _search_or_refresh(self) -> None:
        if self.search_edit.text().strip():
            self._submit_search()
        else:
            self.refresh(True)

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
        sort_column = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._entries))
        queue_items = self._controller.analysis_queue()
        queue_by_path = {
            str(Path(item.path).resolve()): item
            for item in queue_items
            if item.task_type == "analysis"
        }
        translation_by_path = {
            str(Path(item.path).resolve()): item
            for item in queue_items
            if item.task_type == "translation"
        }
        for row, entry in enumerate(self._entries):
            metadata = entry.metadata
            queue_item = queue_by_path.get(str(entry.sidecar_path.resolve()))
            stored_status = str(
                entry.record.get("workflow", {}).get("analysis_status")
                or entry.record.get("analysis", {}).get("status")
                or ""
            )
            version_label = _analysis_version_label(entry.record)
            analysis_status = (
                version_label
                if not queue_item and stored_status == "completed" and version_label
                else {
                    "pending_review": "검토",
                    "organized_pending_analysis": "대기",
                    "analyzing": "중",
                    "failed": "실패",
                }.get(
                    queue_item.status if queue_item else stored_status,
                    version_label or "—",
                )
            )
            translation_item = translation_by_path.get(
                str(entry.sidecar_path.resolve())
            )
            translation_status = "—"
            translations = entry.record.get("translations")
            translations = translations if isinstance(translations, dict) else {}
            analysis_translations = translations.get("analysis")
            analysis_translations = (
                analysis_translations
                if isinstance(analysis_translations, dict)
                else {}
            )
            translated = analysis_translations.get("ko")
            translated = translated if isinstance(translated, dict) else {}
            if translated.get("text"):
                translation_status = (
                    "완료"
                    if str(translated.get("source_hash") or "")
                    == analysis_translation_source_hash(entry.record)
                    else "갱신 필요"
                )
            if translation_item is not None:
                translation_status = {
                    "organized_pending_analysis": "대기",
                    "analyzing": "중",
                    "failed": "실패",
                }.get(translation_item.status, translation_status)
            values = [
                metadata.title,
                ", ".join(metadata.authors),
                str(metadata.year or ""),
                f"{metadata.category} / {metadata.subcategory}",
                analysis_status,
                translation_status,
                _format_library_date(entry.paperpack_created_at),
                _format_library_date(entry.analysis_completed_at),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, str(entry.sidecar_path.resolve()))
                self.table.setItem(row, column, cell)
        self.table.setSortingEnabled(True)
        if sort_column >= 0:
            self.table.sortItems(sort_column, sort_order)
        self.status_label.setText(f"라이브러리 문서 {len(self._entries)}개")
        self.reanalyze_all_button.setEnabled(bool(self._entries))
        if self._entries:
            self.table.selectRow(0)
            self._selection_changed()
        else:
            self.form.set_metadata(None)
            self._render_analysis(None)
            self.save_button.setEnabled(False)
            self.open_button.setEnabled(False)
            self.apply_pdf_button.setEnabled(False)
            self.discard_pdf_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.reanalyze_selected_button.setEnabled(False)
            self.approve_category_button.setEnabled(False)

    def _selected(self) -> LibraryEntry | None:
        row = self.table.currentRow()
        cell = self.table.item(row, 0) if row >= 0 else None
        path = str(cell.data(Qt.UserRole)) if cell is not None else ""
        return next(
            (
                entry
                for entry in self._entries
                if str(entry.sidecar_path.resolve()) == path
            ),
            None,
        )

    def _selected_entries(self) -> list[LibraryEntry]:
        paths = {
            str(cell.data(Qt.UserRole))
            for cell in self.table.selectedItems()
            if cell.column() == 0
        }
        return [
            entry
            for entry in self._entries
            if str(entry.sidecar_path.resolve()) in paths
        ]

    def select_path(self, path: str | Path) -> bool:
        target = Path(path).expanduser().resolve()
        if self.search_edit.text():
            signals_were_blocked = self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(signals_were_blocked)
        self.refresh(True)
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell is not None and Path(str(cell.data(Qt.UserRole))).resolve() == target:
                self.table.selectRow(row)
                self.table.scrollToItem(cell)
                self._selection_changed()
                return True
        return False

    def _selection_changed(self) -> None:
        entries = self._selected_entries()
        entry = entries[0] if len(entries) == 1 else None
        selected_path = str(entry.sidecar_path.resolve()) if entry else ""
        if selected_path != self._translation_path:
            self._translation_path = selected_path
            self.translation_button.blockSignals(True)
            self.translation_button.setChecked(False)
            self.translation_button.blockSignals(False)
        if entry is not None and self._translation_service is not None:
            cached = self._translation_cache.get(selected_path)
            if (
                cached is None
                or cached.source_hash
                != analysis_translation_source_hash(entry.record)
            ):
                self._translation_cache.pop(selected_path, None)
                cached = self._translation_service.cached(entry)
            if cached is not None:
                self._translation_cache[selected_path] = cached
            elif self.translation_button.isChecked():
                self.translation_button.blockSignals(True)
                self.translation_button.setChecked(False)
                self.translation_button.blockSignals(False)
        self.form.set_metadata(entry.metadata if entry else None)
        self.form.setEnabled(entry is not None)
        self.save_button.setEnabled(entry is not None)
        self.open_button.setEnabled(
            any(value.pdf_path.is_file() for value in entries)
        )
        self.reanalyze_selected_button.setEnabled(bool(entries))
        self.delete_button.setEnabled(bool(entries))
        self.approve_category_button.setEnabled(
            bool(
                entry
                and str(
                    entry.record.get("analysis", {}).get("suggested_category") or ""
                ).strip()
            )
        )
        suggestion = (
            str(entry.record.get("analysis", {}).get("suggested_category") or "").strip()
            if entry
            else ""
        )
        self.approve_category_button.setText(
            f"추천 분야 ‘{suggestion}’ 승인 후 재분석"
            if suggestion
            else "AI 추천 연구분야 없음"
        )
        self._update_translation_button(entry)
        self._render_analysis(entry)
        self._refresh_pdf_edit_actions(entries)

    def _update_translation_button(self, entry: LibraryEntry | None) -> None:
        queued = bool(
            entry
            and any(
                item.task_type == "translation"
                and Path(item.path).resolve() == entry.sidecar_path.resolve()
                and item.status in {"organized_pending_analysis", "analyzing"}
                for item in self._controller.analysis_queue()
            )
        )
        can_translate = bool(
            entry is not None
            and self._translation_service is not None
            and self._translation_service.has_source(entry)
            and not _analysis_failed(entry.record)
        )
        self.translation_button.setEnabled(can_translate and not queued)
        self.restore_translation_button.setEnabled(
            bool(entry and _has_previous_analysis_translation(entry.record))
            and not queued
        )
        if queued:
            self.translation_button.setText("AI 번역 대기 중…")
        elif self.translation_button.isChecked():
            self.translation_button.setText("원문 보기")
        elif entry is not None and str(entry.sidecar_path.resolve()) in self._translation_cache:
            self.translation_button.setText("AI 번역 보기")
        else:
            self.translation_button.setText("AI 번역")

    def _translation_toggled(self, checked: bool) -> None:
        entry = self._selected()
        if entry is None:
            return
        path = str(entry.sidecar_path.resolve())
        cached = self._translation_cache.get(path)
        if checked and cached is None:
            if self._translation_service is None:
                self.translation_button.setChecked(False)
                return
            try:
                self._controller.queue_analysis_translation(entry)
            except Exception as exc:
                self.translation_button.blockSignals(True)
                self.translation_button.setChecked(False)
                self.translation_button.blockSignals(False)
                QMessageBox.warning(self, "AI 번역 요청 실패", str(exc))
                return
            self._translation_cache.pop(path, None)
            self.translation_button.blockSignals(True)
            self.translation_button.setChecked(False)
            self.translation_button.blockSignals(False)
            self._update_translation_button(entry)
            self.translation_queued.emit(1)
            self.refresh(True)
            self.status_label.setText(
                "AI 번역을 분석 대기열에 넣었습니다. 다른 AI 작업과 한 건씩 처리합니다."
            )
            return
        self._update_translation_button(entry)
        self._render_analysis(entry)

    def _restore_previous_translation(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        try:
            restored = self._controller.restore_previous_analysis_translation(
                entry
            )
        except Exception as exc:
            QMessageBox.warning(self, "이전 번역 복원 실패", str(exc))
            return
        if not restored:
            self.status_label.setText("복원할 이전 AI 번역이 없습니다.")
            return
        path = str(entry.sidecar_path.resolve())
        self._translation_cache.pop(path, None)
        self.translation_button.blockSignals(True)
        self.translation_button.setChecked(False)
        self.translation_button.blockSignals(False)
        self.refresh(True)
        self.select_path(entry.sidecar_path)
        self.status_label.setText("PaperPack의 이전 AI 번역을 복원했습니다.")

    def is_translation_busy(self) -> bool:
        return False

    def _render_analysis(self, entry: LibraryEntry | None) -> None:
        """선택 문서의 description/analysis 내용을 읽기 전용으로 보여준다."""
        if entry is None:
            self.analysis_view.setHtml(
                "<p style='color:#777'>왼쪽 목록에서 문서를 선택하면 "
                "AI 분석 내용이 표시됩니다.</p>"
            )
            return
        path = str(entry.sidecar_path.resolve())
        translation = self._translation_cache.get(path)
        if self.translation_button.isChecked() and translation is not None:
            provenance = " / ".join(
                value for value in (translation.provider, translation.model) if value
            )
            provenance = (
                f"<p style='color:#777'>AI 번역 · {html.escape(provenance)}"
                f" · {html.escape(_format_library_date(translation.translated_at))}</p>"
                if provenance
                else "<p style='color:#777'>AI 번역</p>"
            )
            translated = html.escape(translation.text).replace("\n", "<br>")
            self.analysis_view.setHtml(
                provenance
                + "<div style='white-space:pre-wrap; line-height:1.45'>"
                + translated
                + "</div>"
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
        workflow_status = str(
            entry.record.get("workflow", {}).get("analysis_status") or ""
        )
        last_attempt = analysis.get("last_attempt")
        failed_attempt = (
            last_attempt
            if isinstance(last_attempt, dict)
            and last_attempt.get("status") == "failed"
            else analysis
            if analysis.get("status") == "failed"
            else {}
        )
        if workflow_status == "failed" or failed_attempt:
            error = str(failed_attempt.get("error") or "").strip()
            fallback = failed_attempt.get("fallback") or analysis.get("fallback") or {}
            sections.append(
                "<h3 style='color:#a33'>AI 요약 실패</h3>"
                "<p>유효한 AI 요약 결과를 저장하지 못했습니다. "
                "아래 내용은 AI 요약이 아니라 원문에서 정규식으로 분리한 정보입니다.</p>"
            )
            if error:
                sections.append(
                    f"<p style='color:#777'>실패 원인: {esc(error)}</p>"
                )
            diagnostics = failed_attempt.get("diagnostics") or {}
            if isinstance(diagnostics, dict):
                kind_labels = {
                    "json_validation": "서지정보 입력 형식 검증 실패",
                    "language_validation": "요약 출력 언어 검증 실패",
                    "timeout": "AI 응답 시간 초과",
                    "authentication": "API 인증 또는 키 오류",
                    "ollama_runtime": "Ollama 실행 또는 연결 오류",
                    "provider_or_application": "AI 제공자 또는 앱 처리 오류",
                }
                stage_labels = {
                    "summary_generation_and_validation": "AI 요약 생성 및 결과 검증",
                }
                strategy_labels = {
                    "direct": "전체 구역 직접 분석",
                    "hierarchical": "구역별 요약 후 최종 합성",
                }
                language_labels = {
                    "ko": "한국어",
                    "source": "논문 원문 언어",
                }
                details = [
                    value
                    for value in (
                        (
                            f"실패 시각: {failed_attempt.get('failed_at')}"
                            if failed_attempt.get("failed_at")
                            else ""
                        ),
                        (
                            "실패 단계: "
                            + stage_labels.get(
                                str(diagnostics.get("stage") or ""),
                                str(diagnostics.get("stage") or ""),
                            )
                            if diagnostics.get("stage")
                            else ""
                        ),
                        (
                            "오류 분류: "
                            + kind_labels.get(
                                str(diagnostics.get("failure_kind") or ""),
                                str(diagnostics.get("failure_kind") or ""),
                            )
                            if diagnostics.get("failure_kind")
                            else ""
                        ),
                        (
                            f"예외 형식: {diagnostics.get('error_type')}"
                            if diagnostics.get("error_type")
                            else ""
                        ),
                        (
                            f"AI: {diagnostics.get('provider')} / "
                            f"{diagnostics.get('model')}"
                            if diagnostics.get("provider")
                            or diagnostics.get("model")
                            else ""
                        ),
                        (
                            f"요청 시도 횟수: {diagnostics.get('request_attempts')}회"
                            if diagnostics.get("request_attempts")
                            else ""
                        ),
                        (
                            "분석 방식: "
                            + strategy_labels.get(
                                str(diagnostics.get("summary_strategy") or ""),
                                str(diagnostics.get("summary_strategy") or ""),
                            )
                            if diagnostics.get("summary_strategy")
                            else ""
                        ),
                        (
                            "출력 언어: "
                            + language_labels.get(
                                str(diagnostics.get("output_language") or ""),
                                str(diagnostics.get("output_language") or ""),
                            )
                            if diagnostics.get("output_language")
                            else ""
                        ),
                        (
                            "포함 구역: "
                            + ", ".join(
                                str(value)
                                for value in diagnostics.get("included_sections") or []
                            )
                            if diagnostics.get("included_sections")
                            else ""
                        ),
                    )
                    if value
                ]
                if details:
                    sections.append(
                        f"<h3>실패 상세</h3>{bullets(details)}"
                    )
            abstract = str(fallback.get("abstract") or "").strip()
            if abstract:
                pages = ", ".join(
                    str(page)
                    for page in fallback.get("abstract_pdf_pages") or []
                )
                page_note = f" · PDF {esc(pages)}쪽" if pages else ""
                abstract_html = "".join(
                    f"<p>{esc(paragraph)}</p>"
                    for paragraph in abstract.split("\n\n")
                    if paragraph.strip()
                )
                sections.append(
                    "<h3>정규식 추출 Abstract"
                    f"<span style='color:#777'>{page_note}</span></h3>"
                    f"{abstract_html}"
                )
            facts = [str(value) for value in fallback.get("facts") or []]
            if facts:
                sections.append(
                    f"<h3>정규식 후보 정보</h3>{bullets(facts)}"
                )
            if not abstract and not facts:
                sections.append(
                    "<p style='color:#999'>표시할 수 있는 정규식 추출 결과가 없습니다.</p>"
                )
            self.analysis_view.setHtml("".join(sections))
            return
        summary = description.get("summary") or ""
        if not summary and not analysis:
            self.analysis_view.setHtml(
                "<p style='color:#777'>아직 AI 분석 결과가 없습니다. "
                "분석 큐에서 백그라운드 분석이 끝나면 이곳에 표시됩니다.</p>"
            )
            return
        if summary:
            summary_paragraphs = "".join(
                f"<p>{esc(paragraph)}</p>"
                for paragraph in str(summary).split("\n\n")
                if paragraph.strip()
            )
            sections.append(f"<h3>요약</h3>{summary_paragraphs}")
        question = description.get("research_question") or ""
        if question:
            question_label = (
                "기술적 과제"
                if entry.metadata.document_type == "patent"
                else "연구 질문"
            )
            sections.append(f"<h3>{question_label}</h3><p>{esc(question)}</p>")
        field_labels = (
            (
                ("구현·실시예", "methods"),
                ("발명의 핵심", "contributions"),
                ("명시된 제약", "limitations"),
                ("키워드", "keywords"),
            )
            if entry.metadata.document_type == "patent"
            else (
                ("방법", "methods"),
                ("핵심 기여", "contributions"),
                ("한계", "limitations"),
                ("키워드", "keywords"),
            )
        )
        for label, key in field_labels:
            values = [str(item) for item in description.get(key) or []]
            if values:
                sections.append(f"<h3>{label}</h3>{bullets(values)}")
        patent_claims = str(analysis.get("patent_claims") or "").strip()
        if entry.metadata.document_type == "patent" and patent_claims:
            displayed_claims = _format_claims_for_display(patent_claims)
            sections.append(
                "<h3>청구항 원문</h3>"
                "<div style='white-space:pre-wrap; overflow-wrap:anywhere; "
                "line-height:1.5; font-family:monospace'>"
                f"{esc(displayed_claims)}"
                "</div>"
            )
        classification = entry.record.get("classification", {})
        ai_tags = [str(value) for value in classification.get("ai_tags") or []]
        if ai_tags:
            sections.append(
                f"<h3>AI 메타태그</h3><p>{esc(' · '.join(ai_tags))}</p>"
            )
        suggestion = str(analysis.get("suggested_category") or "").strip()
        if suggestion:
            sections.append(
                "<h3>추천 연구분야</h3>"
                f"<p><b>{esc(suggestion)}</b> — 승인 전에는 설정에 추가되지 않습니다.</p>"
            )
        provenance = analysis.get("provenance") or entry.record.get(
            "provenance", {}
        ).get("summary")
        if isinstance(provenance, dict) and provenance.get("provider"):
            version_bits = [
                value
                for value in (
                    (
                        f"앱 v{str(provenance.get('app_version')).removeprefix('v')}"
                        if provenance.get("app_version")
                        else ""
                    ),
                    str(provenance.get("prompt_version") or ""),
                )
                if value
            ]
            version_text = (
                f" · {esc(' · '.join(version_bits))}" if version_bits else ""
            )
            sections.append(
                "<p style='color:#777'>"
                f"{esc(provenance.get('provider'))} / {esc(provenance.get('model'))}"
                f"{version_text}"
                f" · {esc(analysis.get('completed_at', ''))}</p>"
            )
        self.analysis_view.setHtml("".join(sections))

    def _reanalyze_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            return
        if QMessageBox.question(
            self,
            "선택 논문 재요약",
            f"선택한 논문 {len(entries)}건을 재요약 대기열에 넣을까요? "
            "현재 분석 결과는 새 분석이 성공할 때까지 유지됩니다.",
        ) != QMessageBox.Yes:
            return
        self._queue_reanalysis(entries)

    def _reanalyze_all(self) -> None:
        entries = self._controller.list_library()
        if not entries:
            return
        if QMessageBox.question(
            self,
            "전체 논문 재요약",
            f"라이브러리 전체 {len(entries)}건을 재요약 대기열에 넣을까요? "
            "백그라운드에서 한 건씩 조용히 처리합니다.",
        ) != QMessageBox.Yes:
            return
        self._queue_reanalysis(entries)

    def _queue_reanalysis(
        self, entries: list[LibraryEntry], *, high: bool = False
    ) -> None:
        try:
            queued, problems = self._controller.queue_reanalysis(entries, high=high)
        except Exception as exc:
            QMessageBox.warning(self, "재요약 요청 실패", str(exc))
            return
        self.status_label.setText(
            f"재요약 {queued}건을 분석 대기열에 넣었습니다."
            + (f" · 제외 {len(problems)}건" if problems else "")
        )
        if problems:
            QMessageBox.warning(
                self, "일부 재요약 요청 실패", "\n".join(problems[:10])
            )
        if queued:
            self.reanalysis_queued.emit(queued)

    def _approve_category(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        suggestion = str(
            entry.record.get("analysis", {}).get("suggested_category") or ""
        ).strip()
        if not suggestion:
            return
        if QMessageBox.question(
            self,
            "추천 연구분야 승인",
            f"‘{suggestion}’을 설정의 연구분야에 추가하고 이 논문을 다시 분석할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            approved = self._controller.approve_category_suggestion(entry)
        except Exception as exc:
            QMessageBox.warning(self, "연구분야 승인 실패", str(exc))
            return
        self._queue_reanalysis([entry], high=True)
        self.status_label.setText(
            f"‘{approved}’을 연구분야에 추가하고 재분석을 요청했습니다."
        )

    def _refresh_pdf_edit_actions(
        self, entries: list[LibraryEntry] | LibraryEntry | None
    ) -> None:
        if isinstance(entries, LibraryEntry):
            entries = [entries]
        packs = [
            entry
            for entry in (entries or [])
            if entry.pdf_path.suffix.casefold() == ".paperpack"
        ]
        self.apply_pdf_button.setEnabled(bool(packs))
        has_working_copy = False
        for entry in packs:
            try:
                if self._controller.paperpack_working_copy(entry.pdf_path) is not None:
                    has_working_copy = True
                    break
            except Exception:
                has_working_copy = True
                break
        self.discard_pdf_button.setEnabled(has_working_copy)

    def _save_selected(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        try:
            updated = self._controller.update_library_metadata(entry, self.form.metadata())
        except Exception as exc:
            QMessageBox.warning(self, "색인 저장 실패", str(exc))
            return
        status = "PaperPack 메타데이터 저장 및 통합 인덱스 재생성을 완료했습니다."
        self.refresh()
        self.status_label.setText(status)
        self.metadata_changed.emit()

    def _show_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and not self.table.item(index.row(), 0).isSelected():
            self.table.clearSelection()
            self.table.selectRow(index.row())
        entries = self._selected_entries()
        if not entries:
            return
        menu = QMenu(self)
        open_action = menu.addAction("sPDF로 열기")
        open_action.triggered.connect(self._open_selected)
        explorer_action = menu.addAction("탐색기에서 열기")
        explorer_action.triggered.connect(self._open_in_explorer)
        menu.addSeparator()
        reanalyze_action = menu.addAction("선택 논문 재요약")
        reanalyze_action.triggered.connect(self._reanalyze_selected)
        if len(entries) == 1:
            suggestion = str(
                entries[0].record.get("analysis", {}).get("suggested_category")
                or ""
            ).strip()
            approve_action = menu.addAction(
                f"AI 추천 연구분야 ‘{suggestion}’ 승인"
                if suggestion
                else "AI 추천 연구분야 없음"
            )
            approve_action.setEnabled(bool(suggestion))
            approve_action.triggered.connect(self._approve_category)
        menu.addSeparator()
        delete_action = menu.addAction("선택 항목을 앱 휴지통으로 이동…")
        delete_action.triggered.connect(self._delete_selected)
        menu.exec_(self.table.viewport().mapToGlobal(position))

    def _open_in_explorer(self) -> None:
        failures: list[str] = []
        opened: set[Path] = set()
        for entry in self._selected_entries():
            target = entry.sidecar_path.resolve()
            if target in opened:
                continue
            opened.add(target)
            if not target.exists():
                failures.append(f"{entry.metadata.title}: 파일이 없습니다.")
                continue
            if not QProcess.startDetached(
                "explorer.exe", ["/select,", str(target)]
            ):
                failures.append(f"{entry.metadata.title}: 탐색기를 열지 못했습니다.")
        if failures:
            QMessageBox.warning(
                self, "탐색기 열기 실패", "\n".join(failures[:10])
            )

    def _delete_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            return
        titles = "\n".join(
            f"• {entry.metadata.title or entry.sidecar_path.stem}"
            for entry in entries[:5]
        )
        if len(entries) > 5:
            titles += f"\n• 외 {len(entries) - 5}건"
        if QMessageBox.question(
            self,
            "라이브러리에서 제거",
            f"선택한 {len(entries)}건의 PaperPack과 저장된 PDF·분석 내용을 "
            "앱 휴지통으로 옮길까요?\n\n"
            f"{titles}\n\n"
            "나중에 수집 화면의 제외·휴지통 목록에서 원래 위치로 복원할 수 있습니다. "
            "감시 폴더에 남아 있는 원본 PDF는 삭제하지 않습니다.",
        ) != QMessageBox.Yes:
            return
        try:
            result = self._controller.trash_library_entries(entries)
        except Exception as exc:
            QMessageBox.warning(self, "앱 휴지통 이동 실패", str(exc))
            return
        self.refresh(True)
        self.status_label.setText(
            f"라이브러리 항목 {result.deleted}건을 앱 휴지통으로 옮겼습니다."
            + (f" · 확인 필요 {len(result.problems)}건" if result.problems else "")
        )
        if result.problems:
            QMessageBox.warning(
                self,
                "일부 휴지통 이동 확인 필요",
                "\n".join(result.problems[:10]),
            )
        if result.deleted:
            self.metadata_changed.emit()

    def _open_selected(self) -> None:
        failures: list[str] = []
        for entry in self._selected_entries():
            try:
                editable_pdf = self._controller.materialize_editable_pdf(entry.pdf_path)
                open_pdf(editable_pdf, self)
            except Exception as exc:
                failures.append(f"{entry.metadata.title}: {exc}")
        self._refresh_pdf_edit_actions(self._selected_entries())
        if failures:
            QMessageBox.warning(self, "일부 sPDF 열기 실패", "\n".join(failures[:10]))

    def _open_row(self, row: int) -> None:
        cell = self.table.item(row, 0)
        if cell is None:
            return
        path = str(cell.data(Qt.UserRole))
        entry = next(
            (
                value
                for value in self._entries
                if str(value.sidecar_path.resolve()) == path
            ),
            None,
        )
        if entry is None:
            return
        try:
            editable_pdf = self._controller.materialize_editable_pdf(entry.pdf_path)
            open_pdf(editable_pdf, self)
            self._refresh_pdf_edit_actions(self._selected_entries())
        except Exception as exc:
            QMessageBox.warning(self, "sPDF 열기 실패", str(exc))

    def _apply_pdf_edit(self) -> None:
        entries = [
            entry
            for entry in self._selected_entries()
            if entry.pdf_path.suffix.casefold() == ".paperpack"
        ]
        if not entries:
            return
        if QMessageBox.question(
            self,
            "PaperPack에 편집본 적용",
            f"선택한 PaperPack {len(entries)}개의 sPDF 저장본을 적용합니다. "
            "sPDF에서 먼저 저장한 변경만 반영됩니다. 계속할까요?",
        ) != QMessageBox.Yes:
            return
        applied = 0
        failures: list[str] = []
        warnings: list[str] = []
        for entry in entries:
            try:
                result = self._controller.apply_paperpack_working_copy(entry.pdf_path)
                applied += 1
                if result.warning:
                    warnings.append(f"{entry.metadata.title}: {result.warning}")
            except Exception as exc:
                failures.append(f"{entry.metadata.title}: {exc}")
        self.refresh(True)
        self.status_label.setText(
            f"편집본 {applied}개를 PaperPack에 적용했습니다."
            + (f" · 실패 {len(failures)}개" if failures else "")
        )
        if warnings or failures:
            QMessageBox.warning(
                self,
                "일부 편집본 적용 확인 필요",
                "\n".join((warnings + failures)[:10]),
            )
        if applied:
            self.metadata_changed.emit()

    def _discard_pdf_edit(self) -> None:
        entries = [
            entry
            for entry in self._selected_entries()
            if entry.pdf_path.suffix.casefold() == ".paperpack"
        ]
        if not entries:
            return
        if QMessageBox.question(
            self,
            "편집본 폐기",
            f"선택한 PaperPack {len(entries)}개의 원본은 유지하고 "
            "sPDF 작업 복사본만 삭제할까요?",
        ) != QMessageBox.Yes:
            return
        removed = 0
        failures: list[str] = []
        for entry in entries:
            try:
                removed += int(
                    self._controller.discard_paperpack_working_copy(entry.pdf_path)
                )
            except Exception as exc:
                failures.append(f"{entry.metadata.title}: {exc}")
        self._refresh_pdf_edit_actions(self._selected_entries())
        self.status_label.setText(
            f"sPDF 편집본 {removed}개를 폐기했습니다."
            if removed
            else "폐기할 편집본이 없습니다."
        )
        if failures:
            QMessageBox.warning(
                self, "일부 편집본 폐기 실패", "\n".join(failures[:10])
            )
