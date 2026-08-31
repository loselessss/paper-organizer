"""Collection review and editable JSON library widgets."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from threading import Event

from PyQt5.QtCore import (
    QItemSelectionModel,
    QMimeData,
    QProcess,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QDrag
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QToolButton,
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
from paper_organizer.application.selection_ai import SelectionAiService
from paper_organizer.integrations.spdf_bridge import (
    SpdfSelection,
    active_spdf_window,
    open_pdf,
)
from paper_organizer.ui.dialog_utils import suppress_context_help_button
from paper_organizer.ui.fluent_style import decorate_action, decorate_button


_REVIEW_DRAG_MIME = "application/x-paper-organizer-review-items"
_SEARCH_LOCATION_LABELS = {
    "title": "제목",
    "summary": "요약",
    "metadata": "서지",
    "body": "본문",
}
_LIBRARY_COLUMNS = (
    ("title", "제목"),
    ("authors", "저자/발명자"),
    ("year", "연도"),
    ("category", "분야"),
    ("analysis_version", "분석 버전"),
    ("translation_status", "번역 상태"),
    ("created_at", "등록일"),
    ("analysis_at", "분석일"),
    ("search_location", "검색 위치"),
)
_LIBRARY_COLUMN_IDS = tuple(column_id for column_id, _label in _LIBRARY_COLUMNS)
_LIBRARY_COLUMN_LABELS = {
    column_id: label for column_id, label in _LIBRARY_COLUMNS
}
_AUTO_BIBLIOGRAPHY_CHECK_VERSION = (2, 3, 0)


def _search_location_text(entry: LibraryEntry) -> str:
    labels: list[str] = []
    for location in entry.search_locations:
        if location == "body" and entry.search_page:
            label = f"본문 {entry.search_page}쪽"
        else:
            label = _SEARCH_LOCATION_LABELS.get(location, location)
        if label not in labels:
            labels.append(label)
    if entry.search_snippet and "문맥 있음" not in labels:
        labels.append("문맥 있음")
    return " · ".join(labels)


def _search_tooltip_text(entry: LibraryEntry) -> str:
    location = _search_location_text(entry)
    parts = []
    if location:
        parts.append(f"검색 위치: {location}")
    if entry.search_snippet:
        parts.append(entry.search_snippet)
    return "\n\n".join(parts)


def _search_result_summary(query: str, entries: list[LibraryEntry]) -> str:
    counts: dict[str, int] = {}
    snippets = 0
    for entry in entries:
        if entry.search_snippet:
            snippets += 1
        for location in entry.search_locations:
            label = _SEARCH_LOCATION_LABELS.get(location, location)
            counts[label] = counts.get(label, 0) + 1
    count_text = " · ".join(
        f"{label} {count}" for label, count in counts.items() if count
    )
    parts = [f"검색 '{query}'", f"결과 {len(entries)}개"]
    if count_text:
        parts.append(count_text)
    if snippets:
        parts.append(f"문맥 {snippets}개")
    return " · ".join(parts)


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
    provenance = _analysis_provenance(record)
    if not isinstance(provenance, dict):
        return ""
    app_version = str(provenance.get("app_version") or "").strip()
    if app_version:
        return f"v{app_version.removeprefix('v')}"
    prompt_version = str(provenance.get("prompt_version") or "")
    marker = next(
        (
            value
            for value in (
                "research-summary-v",
                "review-summary-v",
                "paper-summary-v",
                "patent-summary-v",
            )
            if value in prompt_version
        ),
        "",
    )
    if not marker:
        return ""
    suffix = prompt_version.split(marker, 1)[1]
    number = suffix.split("-", 1)[0]
    return f"v{number}" if number.isdigit() else ""


def _analysis_provenance(record: dict) -> dict:
    analysis = record.get("analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    provenance = analysis.get("provenance")
    if isinstance(provenance, dict):
        return provenance
    root_provenance = record.get("provenance")
    if isinstance(root_provenance, dict):
        summary = root_provenance.get("summary")
        if isinstance(summary, dict):
            return summary
    return {}


def _version_tuple(value: object) -> tuple[int, int, int]:
    text = str(value or "").strip().removeprefix("v")
    parts = re.findall(r"\d+", text)
    numbers = [int(part) for part in parts[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2])


def _bibliography_was_checked_by_current_ai_flow(record: dict) -> bool:
    app_version = _analysis_provenance(record).get("app_version")
    return _version_tuple(app_version) >= _AUTO_BIBLIOGRAPHY_CHECK_VERSION


def _has_verified_bibliography(record: dict) -> bool:
    provenance = record.get("provenance")
    bibliography = (
        provenance.get("bibliography")
        if isinstance(provenance, dict)
        else None
    )
    if isinstance(bibliography, dict) and str(
        bibliography.get("source") or ""
    ).startswith("verified:"):
        return True
    curation = record.get("curation")
    sources = curation.get("field_sources") if isinstance(curation, dict) else {}
    if not isinstance(sources, dict):
        return False
    fields = (
        "bibliography.title",
        "bibliography.authors",
        "bibliography.year",
        "bibliography.venue",
    )
    return any(str(sources.get(field) or "").startswith("verified:") for field in fields)


def _should_show_bibliography_reverify(entry: LibraryEntry | None) -> bool:
    if entry is None or entry.metadata.document_type == "patent":
        return False
    record = entry.record
    return not (
        _bibliography_was_checked_by_current_ai_flow(record)
        or _has_verified_bibliography(record)
    )


def _analysis_html(sections: list[str] | str) -> str:
    body = "".join(sections) if isinstance(sections, list) else sections
    return (
        "<style>"
        "body { font-family: 'Malgun Gothic', 'Segoe UI', sans-serif; "
        "font-size: 10pt; color: #1f1f1f; line-height: 1.38; margin: 0; }"
        "h3 { font-size: 11pt; margin: 12px 0 5px; font-weight: 600; }"
        "p { margin: 3px 0 6px; }"
        "p.bullet { margin: 2px 0 3px 0; }"
        "p.meta { margin: 0; }"
        ".muted { color: #777; }"
        ".empty { color: #777; }"
        ".warning { color: #875f00; }"
        ".error { color: #a33; }"
        "</style>"
        + body
    )


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


def _format_analysis_time(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text.split(".", 1)[0]


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
    "multiple_documents": "복수 문서",
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


def _trash_reason(entry: TrashEntry) -> str:
    actions = {
        "unorganized_duplicate": "확인된 중복 후보로 제외 목록에 보관했습니다.",
        "discarded_new_pdf": "사용자가 새 PDF 검토에서 분석 대상에서 제외했습니다.",
        "library_entry": "사용자가 라이브러리에서 제거해 앱 휴지통에 보관했습니다.",
    }
    action = actions.get(entry.kind, "제외 목록에 보관된 파일입니다.")
    evidence = " ".join(entry.detection_reason.split())
    return f"{action} 판정 근거: {evidence}" if evidence else action


class TrashRestoreDialog(QDialog):
    """Show recoverable excluded PDFs in a spacious, multi-select table."""

    def __init__(self, entries: list[TrashEntry], parent=None) -> None:
        super().__init__(parent)
        suppress_context_help_button(self)
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

        self.table = QTableWidget(len(entries), 5)
        self.table.setHorizontalHeaderLabels(
            ["파일", "판정", "제외 사유", "중복", "추정 제목"]
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
        header.setSectionResizeMode(4, QHeaderView.Stretch)

        for row, entry in enumerate(entries):
            values = [
                entry.original_path.name,
                _trash_judgment(entry),
                _trash_reason(entry),
                _trash_duplicate(entry),
                entry.estimated_title or entry.original_path.stem,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setToolTip(str(entry.original_path))
                elif column == 2:
                    cell.setToolTip(value)
                elif column == 3 and str(entry.duplicate_of) not in {"", "."}:
                    cell.setToolTip(str(entry.duplicate_of))
                self.table.setItem(row, column, cell)
        if entries:
            self.table.selectRow(0)
        self.table.itemSelectionChanged.connect(self._update_restore_button)
        self.table.cellDoubleClicked.connect(
            lambda _row, _column: self._accept_selection()
        )
        layout.addWidget(self.table, 1)

        self.reason_label = QLabel()
        self.reason_label.setWordWrap(True)
        self.reason_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reason_label.setStyleSheet(
            "QLabel { padding: 8px; border: 1px solid #b8bec7; "
            "border-radius: 4px; background: palette(base); }"
        )
        layout.addWidget(QLabel("선택 항목 제외 사유"))
        layout.addWidget(self.reason_label)

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
        row = self.table.currentRow()
        reason = _trash_reason(self._entries[row]) if row >= 0 else ""
        self.reason_label.setText(reason or "선택한 항목의 사유 기록이 없습니다.")

    def _accept_selection(self) -> None:
        if self.selected_entries():
            self.accept()


class ReviewMetadataDialog(QDialog):
    """Edit one review item's metadata without shrinking the review list."""

    def __init__(self, metadata: EditablePaperMetadata, parent=None) -> None:
        super().__init__(parent)
        suppress_context_help_button(self)
        self.setWindowTitle("새 PDF 색인 수정")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self.form = MetadataForm("분석 큐로 보내기 전에 수정할 색인")
        self.form.set_metadata(metadata)
        layout.addWidget(self.form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText("수정 적용")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def metadata(self) -> EditablePaperMetadata:
        return self.form.metadata()


class _ScanWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(
        self,
        controller: LibraryWorkflowController,
        parent=None,
        *,
        require_previous_observation: bool = True,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._require_previous_observation = require_previous_observation

    def run(self) -> None:
        try:
            self.completed.emit(
                self._controller.scan(
                    progress=self.progress.emit,
                    require_previous_observation=self._require_previous_observation,
                )
            )
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
                "skipped",
                "cancelled",
                "failed",
                "ocr_completed",
            }:
                self.queue_changed.emit()
            if result.state == "disabled":
                break
            if (
                result.state
                in {"completed", "translation_completed", "skipped", "cancelled", "failed"}
                and immediate_this_run
                and self._immediate_remaining
            ):
                self._immediate_remaining -= 1
            if (
                result.state
                in {"completed", "translation_completed", "skipped", "cancelled", "failed"}
                and self._immediate_remaining
            ):
                try:
                    manual_interval = self._service.poll_interval("manual")
                except TypeError:
                    manual_interval = 0
                if manual_interval <= 0:
                    continue
                self._wake.wait(manual_interval)
                self._wake.clear()
                continue
            if result.state == "ocr_completed":
                # OCR is only a preparation stage for the same item.
                if self._immediate_remaining:
                    continue
                self._wake.set()
            if self._immediate_remaining:
                wait_seconds = 1
            else:
                try:
                    wait_seconds = self._service.poll_interval("automatic")
                except TypeError:
                    wait_seconds = self._service.poll_interval()
            self._wake.wait(wait_seconds)
            self._wake.clear()
        self._processing = False

    def _notify_started(self, event: AnalysisRunEvent) -> None:
        self.event.emit(event)
        self.queue_changed.emit()


class MetadataForm(QGroupBox):
    def __init__(self, title: str, parent=None, *, compact: bool = False) -> None:
        super().__init__(title, parent)
        self._compact = compact
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        if not title:
            self.setFlat(True)
            self.setStyleSheet(
                "QGroupBox {"
                " background: transparent;"
                " border: 0;"
                " margin-top: 0;"
                " padding-top: 0;"
                "}"
            )
        form = QFormLayout(self)
        self._form_layout = form
        self._analysis_rows: list[tuple[QLabel, QTextBrowser]] = []
        form.setContentsMargins(
            0 if compact else 4 if not title else 12,
            0 if not title else 12,
            0 if compact else 4,
            4,
        )
        form.setVerticalSpacing(4 if not title else 6)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
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
        self._tags: list[str] = []
        self._summary = ""
        self._document_type = "paper"
        self.document_type_combo = QComboBox()
        self.document_type_combo.addItem("연구논문", "research_paper")
        self.document_type_combo.addItem("리뷰논문", "review_paper")
        self.document_type_combo.addItem("특허", "patent")
        self.document_type_combo.currentIndexChanged.connect(
            lambda _index: self._set_document_type(
                str(self.document_type_combo.currentData() or "research_paper"),
                sync_combo=False,
            )
        )
        self._publication_number = ""
        self._application_number = ""
        self.authors_label = QLabel("저자")
        self.venue_label = QLabel("저널/학회")
        self.patent_office_label = QLabel("특허청")
        self.publication_number_label = QLabel("출원/등록번호")
        self.application_number_label = QLabel("출원번호")
        self.assignee_label = QLabel("출원인/권리자")
        self._venue_row_visible = True
        self._patent_rows_visible = False
        self._patent_rows = (
            (self.patent_office_label, self.patent_office_edit),
            (self.publication_number_label, self.publication_number_edit),
            (self.assignee_label, self.assignee_edit),
        )
        form.addRow("제목", self.title_edit)
        form.addRow("문서 유형", self.document_type_combo)
        form.addRow(self.authors_label, self.authors_edit)
        form.addRow("연도", self.year_edit)
        form.addRow(self.venue_label, self.venue_edit)
        if compact:
            category_row = QWidget()
            category_layout = QHBoxLayout(category_row)
            category_layout.setContentsMargins(0, 0, 0, 0)
            category_layout.setSpacing(6)
            category_layout.addWidget(self.category_edit, 1)
            category_layout.addWidget(self.subcategory_edit, 1)
            self.category_label = QLabel("분야/세부분야")
            self.category_row = category_row
            form.addRow(self.category_label, self.category_row)
        else:
            self.category_label = QLabel("분야")
            self.subcategory_label = QLabel("세부분야")
            self.tags_label = QLabel("태그")
            self.category_row = self.category_edit
            form.addRow(self.category_label, self.category_edit)
            form.addRow(self.subcategory_label, self.subcategory_edit)
            form.addRow(self.tags_label, self.tags_edit)

    def set_analysis_rows(self, rows: list[tuple[str, str]]) -> None:
        self.clear_analysis_rows()
        for label_text, body in rows:
            label = QLabel(label_text)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            label_top_margin = 6 if label_text == "분석 정보" else 9
            label.setContentsMargins(0, label_top_margin, 0, 0)
            label.setStyleSheet("QLabel { background: transparent; color: #1f1f1f; }")
            browser = QTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setLineWrapMode(QTextEdit.WidgetWidth)
            browser.document().setDocumentMargin(3 if label_text == "분석 정보" else 8)
            browser.setHtml(_analysis_html(body))
            browser.setFixedHeight(self._analysis_row_height(label_text, body))
            browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            if label_text == "분석 정보":
                browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                padding = 2
            else:
                padding = 4
            browser.setStyleSheet(
                "QTextBrowser {"
                " background-color: #ffffff;"
                " border: 1px solid #d8dde6;"
                " border-radius: 5px;"
                f" padding: {padding}px;"
                "}"
            )
            self._form_layout.addRow(label, browser)
            self._analysis_rows.append((label, browser))

    def clear_analysis_rows(self) -> None:
        for label, browser in reversed(self._analysis_rows):
            try:
                self._form_layout.removeRow(label)
            except (AttributeError, RuntimeError, TypeError):
                label.hide()
                browser.hide()
                label.setParent(None)
                browser.setParent(None)
                label.deleteLater()
                browser.deleteLater()
        self._analysis_rows = []

    def _analysis_row_height(self, label: str, body: str) -> int:
        if label == "분석 정보":
            line_count = 1 + body.count("<br")
            text = html.unescape(re.sub(r"<[^>]+>", " ", body)).strip()
            if len(text) > 95:
                line_count += 1
            return min(64, max(30, 22 + line_count * 18))
        if label in {"추천 연구분야", "AI 메타태그", "키워드"}:
            return 70
        text = re.sub(r"<[^>]+>", " ", body)
        text = html.unescape(re.sub(r"\s+", " ", text)).strip()
        bullet_count = body.count("class='bullet'")
        estimated_lines = max(2, bullet_count + (len(text) // 92))
        if label in {"한계", "근거 한계·연구 공백", "명시된 제약"}:
            return min(190, max(116, 34 + estimated_lines * 21))
        if label in {"핵심 기여", "통합 결론", "발명의 핵심", "AI 요약"}:
            return min(210, max(128, 34 + estimated_lines * 21))
        return min(170, max(82, 34 + estimated_lines * 21))

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
        self._tags = list(value.tags)
        self._summary = value.summary
        self._set_document_type(value.document_type)
        self.setEnabled(metadata is not None)

    def _set_document_type(self, document_type: str, *, sync_combo: bool = True) -> None:
        self._document_type = (
            document_type
            if document_type in {"patent", "research_paper", "review_paper"}
            else "research_paper"
        )
        patent = self._document_type == "patent"
        if sync_combo:
            index = self.document_type_combo.findData(self._document_type)
            if index >= 0:
                self.document_type_combo.blockSignals(True)
                self.document_type_combo.setCurrentIndex(index)
                self.document_type_combo.blockSignals(False)
        self.authors_label.setText("발명자" if patent else "저자")
        self._set_optional_row_visible(
            self.venue_label,
            self.venue_edit,
            not patent,
            "_venue_row_visible",
        )
        if self._patent_rows_visible != patent:
            if patent:
                for label, editor in self._patent_rows:
                    self._insert_before_category(label, editor)
            else:
                for label, editor in reversed(self._patent_rows):
                    self._take_form_row(label, editor)
            self._patent_rows_visible = patent

    def _form_row_index(self, label: QLabel) -> int:
        try:
            row, _role = self._form_layout.getWidgetPosition(label)
            return row
        except (AttributeError, TypeError, RuntimeError):
            return -1

    def _insert_before_category(self, label: QLabel, editor: QWidget) -> None:
        label.setMinimumHeight(0)
        editor.setMinimumHeight(0)
        label.setMaximumHeight(16777215)
        editor.setMaximumHeight(16777215)
        label.show()
        editor.show()
        row = self._form_row_index(self.category_label)
        if row >= 0:
            self._form_layout.insertRow(row, label, editor)
        else:
            self._form_layout.addRow(label, editor)

    def _take_form_row(self, label: QLabel, editor: QWidget) -> None:
        if self._form_row_index(label) < 0:
            return
        try:
            self._form_layout.takeRow(label)
        except (AttributeError, TypeError):
            try:
                self._form_layout.takeRow(self._form_row_index(label))
            except (AttributeError, TypeError):
                self._form_layout.removeRow(label)
        label.hide()
        editor.hide()
        label.setParent(self)
        editor.setParent(self)

    def _set_optional_row_visible(
        self,
        label: QLabel,
        editor: QWidget,
        visible: bool,
        state_attr: str,
    ) -> None:
        if getattr(self, state_attr) == visible:
            return
        if visible:
            self._insert_before_category(label, editor)
        else:
            self._take_form_row(label, editor)
        setattr(self, state_attr, visible)

    def _set_form_row_visible(
        self,
        label: QLabel,
        editor: QWidget,
        visible: bool,
    ) -> None:
        label.setMinimumHeight(0)
        editor.setMinimumHeight(0)
        label.setMaximumHeight(16777215 if visible else 0)
        editor.setMaximumHeight(16777215 if visible else 0)
        if hasattr(self._form_layout, "setRowVisible"):
            try:
                row, _role = self._form_layout.getWidgetPosition(label)
                if row >= 0:
                    self._form_layout.setRowVisible(row, visible)
                    return
            except (AttributeError, TypeError):
                pass
            try:
                self._form_layout.setRowVisible(label, visible)
                return
            except TypeError:
                pass
        label.setVisible(visible)
        editor.setVisible(visible)

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
            tags=(
                self._tags
                if self._compact
                else split_values(self.tags_edit.text())
            ),
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
        self._metadata_overrides: dict[str, EditablePaperMetadata] = {}
        self._worker: _ScanWorker | None = None
        self._schedule_followup = False
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(lambda: self.scan_now(False))

        root = QVBoxLayout(self)
        action_row = QHBoxLayout()
        self.scan_button = QPushButton("새 PDF 검색")
        self.scan_button.clicked.connect(lambda: self.scan_now(True))
        decorate_button(self.scan_button, "search", role="primary")
        self.force_stop_scan_button = QPushButton("강제 종료")
        self.force_stop_scan_button.setToolTip("멈춘 새 PDF 검색 워커를 강제로 종료합니다.")
        self.force_stop_scan_button.setVisible(False)
        self.force_stop_scan_button.clicked.connect(self._force_stop_scan)
        decorate_button(self.force_stop_scan_button, "cancel", role="destructive")
        self.status_label = QLabel()
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_label.setWordWrap(True)
        action_row.addWidget(self.scan_button)
        action_row.addWidget(self.force_stop_scan_button)
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
        self.table.setMinimumHeight(360)
        root.addWidget(self.table, 3)

        self.detail_label = QLabel("검토할 PDF를 선택하세요.")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumWidth(0)
        self.detail_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.detail_label.setMaximumHeight(78)
        root.addWidget(self.detail_label)
        self.form = MetadataForm("이동 전에 수정할 색인")
        self.form.set_metadata(None)
        self.form.hide()

        review_actions = QGridLayout()
        self.select_all_button = QPushButton("전체 선택")
        self.open_button = QPushButton("PDF 열기")
        self.edit_button = QPushButton("색인 수정…")
        self.organize_button = QPushButton("선택 항목 분석 큐로 보내기")
        self.trash_button = QPushButton("제외 목록으로 보내기")
        self.delete_pdf_button = QPushButton("선택 PDF 완전 삭제…")
        self.delete_duplicate_button = QPushButton("기존 중복 항목 완전 삭제…")
        self.restore_button = QPushButton("제외 목록에서 복원…")
        decorate_button(self.select_all_button, "select")
        decorate_button(self.open_button, "open")
        decorate_button(self.edit_button, "edit")
        decorate_button(self.organize_button, "archive", role="primary")
        decorate_button(self.trash_button, "archive")
        decorate_button(self.delete_pdf_button, "delete", role="destructive")
        decorate_button(
            self.delete_duplicate_button, "delete", role="destructive"
        )
        decorate_button(self.restore_button, "restore")
        self.select_all_button.clicked.connect(self.table.selectAll)
        self.open_button.clicked.connect(self._open_selected)
        self.edit_button.clicked.connect(self._edit_selected_metadata)
        self.organize_button.clicked.connect(self._organize_selected)
        self.trash_button.clicked.connect(self._trash_selected)
        self.delete_pdf_button.clicked.connect(self._permanently_delete_selected)
        self.delete_duplicate_button.clicked.connect(
            self._permanently_delete_duplicate
        )
        self.restore_button.clicked.connect(self._restore_trash)
        for button in (
            self.open_button,
            self.edit_button,
            self.organize_button,
            self.trash_button,
            self.delete_pdf_button,
            self.delete_duplicate_button,
        ):
            button.setEnabled(False)
        review_actions.addWidget(self.select_all_button, 0, 0)
        review_actions.addWidget(self.open_button, 0, 1)
        review_actions.addWidget(self.edit_button, 0, 2)
        review_actions.addWidget(self.organize_button, 1, 0, 1, 2)
        review_actions.addWidget(self.trash_button, 1, 2)
        review_actions.addWidget(self.restore_button, 2, 0)
        review_actions.addWidget(self.delete_pdf_button, 2, 1)
        review_actions.addWidget(self.delete_duplicate_button, 2, 2)
        review_actions.setColumnStretch(1, 1)
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

    def scan_now(self, schedule_followup: bool = True) -> None:
        if self.is_busy():
            return
        manual_request = schedule_followup
        self._schedule_followup = False
        self._set_scan_busy(True)
        self.status_label.setText("PDF 안정성과 본문 지문을 확인하고 있습니다…")
        worker = _ScanWorker(
            self._controller,
            self,
            require_previous_observation=not manual_request,
        )
        worker.completed.connect(self._scan_ready)
        worker.failed.connect(self._scan_failed)
        worker.progress.connect(self._scan_progress)
        worker.finished.connect(self._scan_finished)
        self._worker = worker
        worker.start()

    def _set_scan_busy(self, busy: bool) -> None:
        self.scan_button.setEnabled(not busy)
        self.force_stop_scan_button.setVisible(busy)
        self.force_stop_scan_button.setEnabled(busy)

    def _is_current_scan_sender(self) -> bool:
        return self.sender() is self._worker

    def _scan_progress(self, message: str) -> None:
        if not self._is_current_scan_sender():
            return
        self.status_label.setText(message)

    def _scan_ready(self, result: ReviewScan) -> None:
        if not self._is_current_scan_sender():
            return
        self._items = list(result.items)
        self._metadata_overrides = {
            key: value
            for key, value in self._metadata_overrides.items()
            if any(item.identity.file_sha256 == key for item in self._items)
        }
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
        if not self._is_current_scan_sender():
            return
        self.status_label.setText(f"검색 실패: {message}")
        QMessageBox.warning(self, "PDF 검색 실패", message)

    def _scan_finished(self) -> None:
        worker = self.sender()
        if worker is not self._worker:
            if worker is not None:
                worker.deleteLater()
            return
        self._worker = None
        if worker:
            worker.deleteLater()
        self._set_scan_busy(False)

    def _force_stop_scan(self) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        self._schedule_followup = False
        self.force_stop_scan_button.setEnabled(False)
        self.status_label.setText("새 PDF 검색을 강제 종료하는 중입니다…")
        for signal, slot in (
            (worker.completed, self._scan_ready),
            (worker.failed, self._scan_failed),
            (worker.progress, self._scan_progress),
            (worker.finished, self._scan_finished),
        ):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass
        worker.requestInterruption()
        worker.quit()
        if not worker.wait(150):
            worker.terminate()
            worker.wait(1500)
        if worker.isRunning():
            worker.finished.connect(self._scan_finished)
            self.force_stop_scan_button.setEnabled(True)
            self.status_label.setText("강제 종료 요청을 보냈습니다. 워커 정리를 기다리고 있습니다…")
            return
        self._worker = None
        worker.deleteLater()
        self._set_scan_busy(False)
        self.status_label.setText("새 PDF 검색을 강제 종료했습니다.")

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
            self._metadata_for_item(item) if item else None
        )
        self.form.setEnabled(item is not None)
        enabled = bool(selected)
        self.open_button.setEnabled(enabled)
        self.edit_button.setEnabled(item is not None)
        self.organize_button.setEnabled(enabled)
        self.trash_button.setEnabled(enabled)
        self.delete_pdf_button.setEnabled(enabled)
        self.delete_duplicate_button.setEnabled(
            len(selected) == 1
            and selected[0].duplicate is not None
            and selected[0].duplicate.sidecar_path.is_file()
        )
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

    def _metadata_for_item(self, item: ReviewItem) -> EditablePaperMetadata:
        return self._metadata_overrides.get(
            item.identity.file_sha256,
            self._controller.suggest_metadata(item),
        )

    def _edit_selected_metadata(self) -> None:
        item = self._selected()
        if item is None:
            return
        dialog = ReviewMetadataDialog(self._metadata_for_item(item), self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self._metadata_overrides[item.identity.file_sha256] = dialog.metadata()
        self.status_label.setText(f"{item.path.name}의 색인 수정을 적용했습니다.")
        self._selection_changed()

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
            QMessageBox.warning(self, "PDF 열기 실패", str(exc))

    def _show_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and not self.table.item(index.row(), 0).isSelected():
            self.table.clearSelection()
            self.table.selectRow(index.row())
        items = self._selected_items()
        if not items:
            return
        menu = QMenu(self)
        open_action = menu.addAction("새 PDF 열기")
        open_action.triggered.connect(self._open_selected)
        if len(items) == 1 and items[0].duplicate is not None:
            duplicate = items[0].duplicate
            existing_action = menu.addAction("기존 라이브러리 분석 보기")
            existing_action.setEnabled(duplicate.sidecar_path.is_file())
            existing_action.triggered.connect(
                lambda: self.library_requested.emit(str(duplicate.sidecar_path))
            )
        menu.addSeparator()
        if len(items) == 1:
            edit_action = menu.addAction("색인 수정…")
            edit_action.triggered.connect(self._edit_selected_metadata)
        organize_action = menu.addAction("분석 큐로 보내기")
        organize_action.triggered.connect(self._organize_selected)
        trash_action = menu.addAction("제외 목록으로 보내기")
        trash_action.triggered.connect(self._trash_selected)
        delete_action = menu.addAction("선택 PDF 완전 삭제…")
        delete_action.triggered.connect(self._permanently_delete_selected)
        if len(items) == 1 and items[0].duplicate is not None:
            delete_duplicate_action = menu.addAction("기존 중복 항목 완전 삭제…")
            delete_duplicate_action.setEnabled(
                items[0].duplicate.sidecar_path.is_file()
            )
            delete_duplicate_action.triggered.connect(
                self._permanently_delete_duplicate
            )
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
                    self._metadata_for_item(item)
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

    def _permanently_delete_selected(self) -> None:
        items = self._selected_items()
        if not items:
            return
        names = "\n".join(f"• {item.path.name}" for item in items[:8])
        if len(items) > 8:
            names += f"\n• 외 {len(items) - 8}개"
        if QMessageBox.warning(
            self,
            "선택 PDF 완전 삭제",
            f"다음 PDF {len(items)}개를 복구할 수 없게 완전히 삭제합니다.\n\n"
            f"{names}\n\n이 작업은 되돌릴 수 없습니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        try:
            result = self._controller.permanently_delete_review_items(items)
        except Exception as exc:
            QMessageBox.warning(self, "PDF 완전 삭제 실패", str(exc))
            return
        self.status_label.setText(
            f"PDF {result.deleted}개를 완전히 삭제했습니다."
            + (f" · 실패 {len(result.problems)}개" if result.problems else "")
        )
        if result.problems:
            QMessageBox.warning(self, "일부 PDF 삭제 실패", "\n".join(result.problems[:10]))
        self.queue_changed.emit()
        self.scan_now(False)

    def _permanently_delete_duplicate(self) -> None:
        items = self._selected_items()
        if len(items) != 1 or items[0].duplicate is None:
            return
        duplicate = items[0].duplicate
        if QMessageBox.warning(
            self,
            "기존 중복 항목 완전 삭제",
            "다음 기존 라이브러리 항목을 복구할 수 없게 완전히 삭제합니다.\n\n"
            f"• {duplicate.title or duplicate.sidecar_path.name}\n"
            f"• {duplicate.sidecar_path}\n\n"
            "새로 발견된 PDF는 남습니다. 이 작업은 되돌릴 수 없습니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        try:
            result = self._controller.permanently_delete_duplicate_reference(items[0])
        except Exception as exc:
            QMessageBox.warning(self, "기존 중복 항목 삭제 실패", str(exc))
            return
        self.status_label.setText(
            f"기존 중복 항목 {result.deleted}건을 완전히 삭제했습니다."
            + (f" · 확인 필요 {len(result.problems)}건" if result.problems else "")
        )
        if result.problems:
            QMessageBox.warning(
                self,
                "기존 중복 항목 삭제 확인 필요",
                "\n".join(result.problems[:10]),
            )
        self.library_changed.emit()
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


class _ElidedTableTextDelegate(QStyledItemDelegate):
    """Paint table text once to avoid Qt stylesheet elide overdraw artifacts."""

    def paint(self, painter, option, index) -> None:
        item_option = QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        text = item_option.text
        item_option.text = ""
        widget = item_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, item_option, painter, widget)
        rect = style.subElementRect(
            QStyle.SE_ItemViewItemText,
            item_option,
            widget,
        ).adjusted(5, 0, -5, 0)
        if rect.width() <= 0:
            return
        elided = item_option.fontMetrics.elidedText(
            text,
            item_option.textElideMode,
            rect.width(),
        )
        painter.save()
        painter.setFont(item_option.font)
        painter.setPen(item_option.palette.color(item_option.palette.Text))
        painter.drawText(
            rect,
            int(item_option.displayAlignment) | Qt.AlignVCenter,
            elided,
        )
        painter.restore()


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
            ["우선순위", "상태", "제목", "대기/실패 사유", "파일"]
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
        actions = QGridLayout()
        refresh_button = QPushButton("새로고침")
        select_all_button = QPushButton("전체 선택")
        self.priority_button = QPushButton("최우선으로 표시")
        self.run_now_button = QPushButton("선택 항목 바로 분석")
        self.remove_button = QPushButton("선택 항목 큐에서 제외")
        self.retry_button = QPushButton("실패 항목 다시 분석")
        self.background_button = QPushButton("백그라운드 분석 시작")
        self.immediate_stop_button = QPushButton("즉시 정지")
        decorate_button(refresh_button, "refresh")
        decorate_button(select_all_button, "select")
        decorate_button(self.priority_button, "priority")
        decorate_button(self.run_now_button, "ai", role="primary")
        decorate_button(self.remove_button, "cancel")
        decorate_button(self.retry_button, "refresh")
        decorate_button(self.background_button, "ai", role="primary")
        decorate_button(self.immediate_stop_button, "stop", role="destructive")
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
        actions.addWidget(refresh_button, 0, 0)
        actions.addWidget(select_all_button, 0, 1)
        actions.addWidget(self.priority_button, 0, 2)
        actions.addWidget(self.run_now_button, 1, 0)
        actions.addWidget(self.remove_button, 1, 1)
        actions.addWidget(self.retry_button, 1, 2)
        actions.addWidget(self.background_button, 2, 0)
        actions.addWidget(self.immediate_stop_button, 2, 1)
        actions.setColumnStretch(2, 1)
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
                item.last_error
                if item.status in {"organized_pending_analysis", "failed"}
                else "",
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
        busy = bool(self._current_analysis_title) or analyzing > 0
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


class SelectionAiWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, service, selection, action, allow_cloud_once=False, parent=None):
        super().__init__(parent)
        self._service = service
        self._selection = selection
        self._action = action
        self._allow_cloud_once = allow_cloud_once
        self._cancel = Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result = self._service.run(
                self._selection,
                self._action,
                allow_cloud_once=self._allow_cloud_once,
                cancel_event=self._cancel,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class SelectionAiDialog(QDialog):
    """Show ephemeral translation and summary actions outside the library pane."""

    action_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("선택 영역 번역·요약")
        suppress_context_help_button(self)
        self.resize(720, 560)
        layout = QVBoxLayout(self)

        self.selection_label = QLabel()
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        self.selection_preview = QTextEdit()
        self.selection_preview.setReadOnly(True)
        self.selection_preview.setPlaceholderText("sPDF에서 선택한 텍스트가 표시됩니다.")
        layout.addWidget(self.selection_preview, 2)

        actions = QHBoxLayout()
        self.translate_button = QPushButton("번역")
        self.summary_button = QPushButton("요약")
        self.copy_button = QPushButton("결과 복사")
        self.cancel_button = QPushButton("요청 취소")
        self.copy_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.translate_button.clicked.connect(
            lambda: self.action_requested.emit("translate")
        )
        self.summary_button.clicked.connect(
            lambda: self.action_requested.emit("summarize")
        )
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.result_view.toPlainText())
        )
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        actions.addWidget(self.translate_button)
        actions.addWidget(self.summary_button)
        actions.addStretch(1)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)

        result_group = QGroupBox("AI 결과")
        result_layout = QVBoxLayout(result_group)
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setPlaceholderText("번역 또는 요약 결과가 여기에 표시됩니다.")
        result_layout.addWidget(self.result_view)
        layout.addWidget(result_group, 3)

        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.rejected.connect(self.close)
        layout.addWidget(close_buttons)

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

    def set_selection(
        self,
        selection: SpdfSelection | None,
        *,
        service_available: bool,
    ) -> None:
        available = bool(
            selection is not None
            and selection.text.strip()
            and not selection.requires_ocr
            and service_available
        )
        self.translate_button.setEnabled(available)
        self.summary_button.setEnabled(available)
        if selection is None:
            self.selection_label.setText("sPDF에서 번역하거나 요약할 텍스트를 선택하세요.")
            self.selection_preview.clear()
        elif selection.requires_ocr:
            self.selection_label.setText(
                f"PDF {selection.pdf_page}쪽 선택 영역에는 텍스트 레이어가 없습니다. "
                "sPDF에서 OCR을 먼저 실행하세요."
            )
            self.selection_preview.clear()
        else:
            self.selection_label.setText(
                f"PDF {selection.pdf_page}쪽 · {len(selection.text)}자"
            )
            self.selection_preview.setPlainText(selection.text)

    def set_busy(self, busy: bool) -> None:
        self.translate_button.setEnabled(not busy and self.translate_button.isEnabled())
        self.summary_button.setEnabled(not busy and self.summary_button.isEnabled())
        self.cancel_button.setEnabled(busy)

    def show_result(self, text: str) -> None:
        self.result_view.setPlainText(text)
        self.copy_button.setEnabled(bool(text.strip()))


class LibraryWidget(QWidget):
    metadata_changed = pyqtSignal()
    reanalysis_queued = pyqtSignal(int)
    translation_queued = pyqtSignal(int)
    natural_search_requested = pyqtSignal(str)
    pdf_export_requested = pyqtSignal()
    search_rebuild_requested = pyqtSignal()
    legacy_migration_requested = pyqtSignal()
    actions_changed = pyqtSignal()

    def __init__(
        self,
        controller: LibraryWorkflowController,
        parent=None,
        *,
        translation_service: LibraryTranslationService | None = None,
        selection_ai: SelectionAiService | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._translation_service = translation_service
        self._selection_ai = selection_ai
        self._spdf_selection: SpdfSelection | None = None
        self._selection_worker: SelectionAiWorker | None = None
        self._selection_dialog: SelectionAiDialog | None = None
        self._tiled_spdf_window = None
        self._tiled_spdf_geometry = None
        self._translation_path = ""
        self._translation_cache: dict[str, LibraryTranslation] = {}
        self._entries: list[LibraryEntry] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.library_title_label = QLabel("라이브러리")
        self.library_title_label.setObjectName("libraryTitleLabel")
        self.library_count_label = QLabel("문서 0개")
        self.library_count_label.setObjectName("libraryCountLabel")
        self.status_label = QLabel("")
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "제목·저자·키워드 검색 · 자연어 질문 검색도 가능"
        )
        self.search_edit.setClearButtonEnabled(False)
        self.clear_search_button = QPushButton("")
        self.clear_search_button.setFixedWidth(36)
        self.clear_search_button.setToolTip("검색어 지우기")
        decorate_button(self.clear_search_button, "cancel")
        self.clear_search_button.clicked.connect(self.search_edit.clear)
        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(self._search_or_refresh)
        decorate_button(refresh_button, "refresh")
        self.search_edit.returnPressed.connect(self._submit_search)
        self.search_edit.textChanged.connect(self._search_text_changed)
        search_row.addWidget(self.library_title_label)
        search_row.addWidget(self.library_count_label)
        search_row.addSpacing(8)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.clear_search_button)
        search_row.addWidget(refresh_button)
        search_row.addWidget(self.status_label, 1)
        root.addLayout(search_row)
        self.search_result_bar = QFrame()
        self.search_result_bar.setObjectName("librarySearchResultBar")
        self.search_result_bar.setFrameShape(QFrame.StyledPanel)
        self.search_result_bar.setStyleSheet(
            "QFrame#librarySearchResultBar {"
            " background-color: #f7f7f7;"
            " border: 1px solid #e0e0e0;"
            " border-radius: 4px;"
            "}"
            "QLabel { color: #5f5f5f; }"
        )
        search_result_layout = QHBoxLayout(self.search_result_bar)
        search_result_layout.setContentsMargins(9, 5, 9, 5)
        self.search_result_label = QLabel("")
        self.search_result_label.setWordWrap(True)
        search_result_layout.addWidget(self.search_result_label, 1)
        self.search_result_bar.setVisible(False)
        root.addWidget(self.search_result_bar)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [label for _column_id, label in _LIBRARY_COLUMNS]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setItemDelegate(_ElidedTableTextDelegate(self.table))
        self.table.setTextElideMode(Qt.ElideRight)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_column_menu)
        header.sectionMoved.connect(self._library_column_moved)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 260)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(2, 72)
        self.table.setColumnWidth(8, 165)
        self._applying_column_layout = False
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
        detail_layout.setSpacing(4)
        self.save_button = QPushButton("색인 편집 저장 및 재색인")
        self.save_button.setToolTip("현재 색인 편집 내용을 저장하고 통합 색인을 다시 만듭니다.")
        self.reverify_bibliography_button = QPushButton("서지 재검증")
        self.reverify_bibliography_button.setToolTip(
            "선택한 논문의 제목·저자·연도·저널을 PubMed·Crossref로 다시 확인합니다. "
            "직접 입력한 필드는 덮어쓰지 않습니다."
        )
        save_row = QHBoxLayout()
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.setSpacing(6)
        save_row.addStretch(1)
        save_row.addWidget(self.reverify_bibliography_button)
        save_row.addWidget(self.save_button)
        detail_layout.addLayout(save_row)
        self.type_suggestion_label = QLabel()
        self.type_suggestion_label.setWordWrap(True)
        self.type_suggestion_label.setVisible(False)
        detail_layout.addWidget(self.type_suggestion_label)
        self.form = MetadataForm("", compact=True)
        self.form.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.form.set_metadata(None)
        detail_layout.addWidget(self.form, 1)
        self.open_with_ai_button = QPushButton("PDF + AI")
        self.open_with_ai_button.setToolTip("AI 번역/요약 창과 함께 PDF를 엽니다.")
        self.open_button = QPushButton("PDF 열기")
        self.open_button.setToolTip("선택한 논문 PDF를 엽니다.")
        self.selection_ai_button = QPushButton("선택 AI")
        self.selection_ai_button.setToolTip(
            "sPDF에서 텍스트를 선택하면 번역·요약 창이 자동으로 열립니다. "
            "닫은 창을 다시 열 때 사용하세요."
        )
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
        self.apply_pdf_button = QPushButton("적용")
        self.apply_pdf_button.setToolTip("PDF 편집본을 PaperPack에 적용합니다.")
        self.discard_pdf_button = QPushButton("편집 취소")
        self.discard_pdf_button.setToolTip(
            "PDF 편집 작업본만 제거합니다. PaperPack 원본은 유지됩니다."
        )
        self.delete_button = QPushButton("휴지통")
        self.delete_button.setToolTip("선택 항목을 앱 휴지통으로 이동합니다.")
        self.permanent_delete_button = QPushButton("완전 삭제")
        self.permanent_delete_button.setToolTip("선택 항목을 완전히 삭제합니다.")
        self.reanalyze_selected_button = QPushButton("선택 재요약")
        self.reanalyze_selected_button.setToolTip("선택한 논문을 다시 요약합니다.")
        self.reanalyze_all_button = QPushButton("전체 재요약")
        self.reanalyze_all_button.setToolTip("현재 라이브러리의 모든 논문을 다시 요약합니다.")
        self.paperpack_manage_button = QToolButton()
        self.paperpack_manage_button.setText("PaperPack 관리")
        self.paperpack_manage_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.paperpack_manage_button.setPopupMode(QToolButton.InstantPopup)
        self.paperpack_manage_button.setToolTip("PDF 환원, 검색 색인 재구축, 구버전 마이그레이션")
        manage_menu = QMenu(self.paperpack_manage_button)
        pdf_export_action = manage_menu.addAction("PDF 환원…")
        decorate_action(pdf_export_action, "pdf")
        pdf_export_action.triggered.connect(self.pdf_export_requested.emit)
        rebuild_action = manage_menu.addAction("검색 색인 재구축")
        decorate_action(rebuild_action, "refresh")
        rebuild_action.triggered.connect(self.search_rebuild_requested.emit)
        migration_action = manage_menu.addAction("구버전 PaperPack 마이그레이션")
        decorate_action(migration_action, "archive")
        migration_action.triggered.connect(self.legacy_migration_requested.emit)
        self.paperpack_manage_button.setMenu(manage_menu)
        decorate_button(self.save_button, "save", role="primary")
        decorate_button(self.reverify_bibliography_button, "refresh")
        decorate_button(self.open_button, "open")
        decorate_button(self.open_with_ai_button, "ai")
        decorate_button(self.selection_ai_button, "ai")
        decorate_button(self.translation_button, "translate")
        decorate_button(self.restore_translation_button, "restore")
        decorate_button(self.apply_pdf_button, "save", role="primary")
        decorate_button(self.discard_pdf_button, "cancel")
        decorate_button(self.delete_button, "delete")
        decorate_button(self.permanent_delete_button, "delete", role="destructive")
        decorate_button(self.reanalyze_selected_button, "refresh")
        decorate_button(self.reanalyze_all_button, "refresh")
        decorate_button(self.paperpack_manage_button, "archive")
        self.translation_button.toggled.connect(self._translation_toggled)
        self.restore_translation_button.clicked.connect(
            self._restore_previous_translation
        )
        self.save_button.clicked.connect(self._save_selected)
        self.save_button.setEnabled(False)
        self.reverify_bibliography_button.clicked.connect(
            self._reverify_selected_bibliography
        )
        self.reverify_bibliography_button.setEnabled(False)
        self.reverify_bibliography_button.setVisible(False)
        self.open_button.clicked.connect(self._open_selected)
        self.open_with_ai_button.clicked.connect(self._open_selected_with_ai)
        self.selection_ai_button.clicked.connect(
            lambda: self._open_selection_ai_dialog(activate=True)
        )
        self.apply_pdf_button.clicked.connect(self._apply_pdf_edit)
        self.discard_pdf_button.clicked.connect(self._discard_pdf_edit)
        self.delete_button.clicked.connect(self._delete_selected)
        self.permanent_delete_button.clicked.connect(
            self._permanently_delete_library_selected
        )
        self.reanalyze_selected_button.clicked.connect(self._reanalyze_selected)
        self.reanalyze_all_button.clicked.connect(self._reanalyze_all)
        self.open_button.setEnabled(False)
        self.open_with_ai_button.setEnabled(False)
        self.selection_ai_button.setEnabled(False)
        self.apply_pdf_button.setEnabled(False)
        self.discard_pdf_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.permanent_delete_button.setEnabled(False)
        self.reanalyze_selected_button.setEnabled(False)
        self.reanalyze_all_button.setEnabled(False)
        self.analysis_view = QTextBrowser()
        self.analysis_view.setVisible(False)

        splitter = QSplitter(Qt.Horizontal)
        self.library_splitter = splitter
        splitter.addWidget(self.table)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setMinimumWidth(0)
        detail_scroll.setMinimumHeight(0)
        detail_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        detail_scroll.setWidget(detail_panel)
        splitter.addWidget(detail_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setChildrenCollapsible(True)
        splitter.setCollapsible(0, True)
        splitter.setCollapsible(1, True)
        self.table.setMinimumWidth(0)
        detail_panel.setMinimumWidth(0)
        splitter.setSizes([640, 640])
        root.addWidget(splitter, 1)
        self._apply_library_column_preferences()
        self._render_analysis(None)
        self.refresh()

    def _set_analysis_rows(self, rows: list[tuple[str, str]]) -> None:
        aggregate = []
        for label, body in rows:
            aggregate.append(f"<h3>{html.escape(label)}</h3>{body}")
        self.analysis_view.setHtml(_analysis_html(aggregate))
        self.form.set_analysis_rows(rows)

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

    def _column_index(self, column_id: str) -> int:
        try:
            return _LIBRARY_COLUMN_IDS.index(column_id)
        except ValueError:
            return -1

    def _library_column_id(self, logical_index: int) -> str:
        if 0 <= logical_index < len(_LIBRARY_COLUMN_IDS):
            return _LIBRARY_COLUMN_IDS[logical_index]
        return ""

    def _apply_library_column_preferences(self) -> None:
        settings = self._controller.settings()
        order = [
            column_id
            for column_id in settings.library_column_order
            if column_id in _LIBRARY_COLUMN_IDS
        ]
        order.extend(column_id for column_id in _LIBRARY_COLUMN_IDS if column_id not in order)
        hidden = {
            column_id
            for column_id in settings.library_hidden_columns
            if column_id in _LIBRARY_COLUMN_IDS and column_id != "title"
        }
        header = self.table.horizontalHeader()
        self._applying_column_layout = True
        try:
            for visual_index, column_id in enumerate(order):
                logical_index = self._column_index(column_id)
                current_visual = header.visualIndex(logical_index)
                if logical_index >= 0 and current_visual != visual_index:
                    header.moveSection(current_visual, visual_index)
            for column_id in _LIBRARY_COLUMN_IDS:
                logical_index = self._column_index(column_id)
                if logical_index >= 0:
                    self.table.setColumnHidden(logical_index, column_id in hidden)
        finally:
            self._applying_column_layout = False

    def _save_library_column_preferences(self) -> None:
        if self._applying_column_layout:
            return
        header = self.table.horizontalHeader()
        ordered = sorted(
            range(len(_LIBRARY_COLUMN_IDS)),
            key=header.visualIndex,
        )
        order = [self._library_column_id(index) for index in ordered]
        hidden = [
            column_id
            for column_id in _LIBRARY_COLUMN_IDS
            if column_id != "title"
            and self.table.isColumnHidden(self._column_index(column_id))
        ]
        try:
            self._controller.save_library_column_preferences(
                order=order,
                hidden=hidden,
            )
        except Exception as exc:
            self.status_label.setText(f"라이브러리 열 설정 저장 실패: {exc}")

    def _library_column_moved(
        self, _logical_index: int, _old_visual_index: int, _new_visual_index: int
    ) -> None:
        if self.search_edit.text().strip():
            return
        self._save_library_column_preferences()

    def _show_column_menu(self, position) -> None:
        menu = QMenu(self)
        actions = []
        for column_id, label in _LIBRARY_COLUMNS:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(self._column_index(column_id)))
            action.setEnabled(column_id != "title")
            actions.append((action, column_id))
        menu.addSeparator()
        reset_action = menu.addAction("기본 열 구성으로 되돌리기")
        selected = menu.exec_(self.table.horizontalHeader().mapToGlobal(position))
        if selected is None:
            return
        if selected is reset_action:
            try:
                self._controller.save_library_column_preferences(
                    order=[],
                    hidden=[],
                )
            except Exception as exc:
                self.status_label.setText(f"라이브러리 열 설정 저장 실패: {exc}")
                return
            self._apply_library_column_preferences()
            return
        for action, column_id in actions:
            if selected is action:
                logical_index = self._column_index(column_id)
                self.table.setColumnHidden(logical_index, not action.isChecked())
                self._save_library_column_preferences()
                return

    def refresh(self, force: bool = False) -> None:
        selected_paths = {
            str(cell.data(Qt.UserRole))
            for cell in self.table.selectedItems()
            if cell.column() == 0
        }
        current_cell = self.table.item(self.table.currentRow(), 0)
        current_path = (
            str(current_cell.data(Qt.UserRole)) if current_cell is not None else ""
        )
        vertical_position = self.table.verticalScrollBar().value()
        horizontal_position = self.table.horizontalScrollBar().value()
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
        signals_were_blocked = self.table.blockSignals(True)
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
                    "bibliography_only": "서지만 정리",
                    "analyzing": "중",
                    "failed": "실패",
                    "skipped_multi_document": "복수 문서 · 요약 제외",
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
            search_location_text = _search_location_text(entry)
            search_tooltip = _search_tooltip_text(entry)
            values = [
                metadata.title,
                ", ".join(metadata.authors),
                str(metadata.year or ""),
                f"{metadata.category} / {metadata.subcategory}",
                analysis_status,
                translation_status,
                _format_library_date(entry.paperpack_created_at),
                _format_library_date(entry.analysis_completed_at),
                search_location_text,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, str(entry.sidecar_path.resolve()))
                if entry.search_locations:
                    cell.setToolTip(search_tooltip)
                    if column == 8:
                        cell.setBackground(QColor(232, 244, 255))
                        cell.setForeground(QColor(23, 63, 104))
                    elif column in {0, 1}:
                        cell.setBackground(QColor(248, 252, 255))
                self.table.setItem(row, column, cell)
        self.table.setSortingEnabled(True)
        if sort_column >= 0:
            self.table.sortItems(sort_column, sort_order)
        selection_model = self.table.selectionModel()
        selection_model.clearSelection()
        current_index = None
        first_selected_index = None
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            path = str(cell.data(Qt.UserRole)) if cell is not None else ""
            index = self.table.model().index(row, 0)
            if path in selected_paths:
                selection_model.select(
                    index,
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
                first_selected_index = first_selected_index or index
            if path == current_path:
                current_index = index
        self.library_count_label.setText(f"문서 {len(self._entries)}개")
        self._update_search_result_bar(query)
        if self.status_label.text().startswith("라이브러리 문서 "):
            self.status_label.clear()
        self.reanalyze_all_button.setEnabled(bool(self._entries))
        if self._entries:
            if current_index is not None:
                selection_model.setCurrentIndex(
                    current_index,
                    QItemSelectionModel.NoUpdate,
                )
            elif first_selected_index is not None:
                selection_model.setCurrentIndex(
                    first_selected_index,
                    QItemSelectionModel.NoUpdate,
                )
            else:
                self.table.selectRow(0)
            self.table.verticalScrollBar().setValue(vertical_position)
            self.table.horizontalScrollBar().setValue(horizontal_position)
            self.table.blockSignals(signals_were_blocked)
            self._selection_changed()
        else:
            self.table.blockSignals(signals_were_blocked)
            self.form.set_metadata(None)
            self._render_analysis(None)
            self.save_button.setEnabled(False)
            self.reverify_bibliography_button.setEnabled(False)
            self.reverify_bibliography_button.setVisible(False)
            self.open_button.setEnabled(False)
            self.apply_pdf_button.setEnabled(False)
            self.discard_pdf_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.permanent_delete_button.setEnabled(False)
            self.reanalyze_selected_button.setEnabled(False)

    def _update_search_result_bar(self, query: str) -> None:
        if query:
            self.search_result_bar.setVisible(True)
            self.search_result_label.setText(
                _search_result_summary(query, self._entries)
            )
        else:
            self.search_result_bar.setVisible(False)
            self.search_result_label.clear()

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
        candidate = (
            self._controller.suggested_document_type(entry)
            if entry and hasattr(self._controller, "suggested_document_type")
            else None
        )
        type_labels = {
            "research_paper": "연구논문",
            "review_paper": "리뷰논문",
            "patent": "특허",
        }
        self.type_suggestion_label.setVisible(bool(candidate))
        self.type_suggestion_label.setText(
            f"자동 재분류 후보: {type_labels.get(candidate, candidate)} — 위 문서 유형을 선택하고 저장하면 확정됩니다."
            if candidate
            else ""
        )
        self.form.setEnabled(entry is not None)
        self.save_button.setEnabled(entry is not None)
        show_bibliography_reverify = _should_show_bibliography_reverify(entry)
        self.reverify_bibliography_button.setVisible(show_bibliography_reverify)
        self.reverify_bibliography_button.setEnabled(show_bibliography_reverify)
        self.open_button.setEnabled(
            any(value.pdf_path.is_file() for value in entries)
        )
        self.open_with_ai_button.setEnabled(
            len(entries) == 1
            and entries[0].pdf_path.is_file()
            and self._selection_ai is not None
        )
        self.reanalyze_selected_button.setEnabled(bool(entries))
        self.delete_button.setEnabled(bool(entries))
        self.permanent_delete_button.setEnabled(bool(entries))
        self._update_translation_button(entry)
        self._render_analysis(entry)
        self._refresh_pdf_edit_actions(entries)
        self.actions_changed.emit()

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
        self.actions_changed.emit()

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
        self.actions_changed.emit()

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
            self._set_analysis_rows(
                [
                    (
                        "AI 요약",
                        "<p class='empty'>왼쪽 목록에서 문서를 선택하면 "
                        "AI 요약이 표시됩니다.</p>",
                    )
                ]
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
            self._set_analysis_rows(
                [
                    (
                        "AI 번역",
                        provenance
                        + "<div style='white-space:pre-wrap; line-height:1.45'>"
                        + translated
                        + "</div>",
                    )
                ]
            )
            return
        description = entry.record.get("description", {})
        analysis = entry.record.get("analysis", {})
        esc = lambda value: html.escape(str(value or ""))

        def bullets(values) -> str:
            cleaned = [
                re.sub(r"^\s*(?:[-*•·‣▪◦]+|\d+[.)])\s*", "", str(item)).strip()
                for item in values
            ]
            cleaned = [item for item in cleaned if item]
            if not cleaned:
                return "<p style='color:#999'>없음</p>"
            return "".join(
                f"<p class='bullet'>•&nbsp;{esc(item)}</p>"
                for item in cleaned
            )

        def summary_points(value: object) -> list[str]:
            text = str(value or "").strip()
            if not text:
                return []
            paragraphs = [
                re.sub(r"\s+", " ", paragraph).strip()
                for paragraph in re.split(r"\n\s*\n+", text)
                if paragraph.strip()
            ]
            if len(paragraphs) > 1:
                return paragraphs
            sentences = [
                part.strip()
                for part in re.split(r"(?<=[.!?。！？])\s+", paragraphs[0])
                if part.strip()
            ]
            return sentences if len(sentences) > 1 else paragraphs

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
        if workflow_status == "skipped_multi_document":
            reason = str(
                entry.record.get("workflow", {}).get("review_reason") or ""
            ).strip()
            sections.append(
                "<h3 style='color:#875f00'>복수 문서 묶음 · AI 요약 제외</h3>"
                "<p>서로 다른 문서가 한 PDF에 들어 있어 AI 요약을 실행하지 않습니다. "
                "필요하면 문서를 각각 분리한 뒤 다시 가져오세요.</p>"
            )
            if reason:
                sections.append(
                    f"<p style='color:#777'>감지 근거: {esc(reason)}</p>"
                )
            failed_attempt = {}
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
                    "abstract_only": "서지정보 + Abstract 정리",
                    "bibliography_only": "서지정보만(Abstract 없음)",
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
            rows = [("분석 상태", "".join(sections))]
            self._set_analysis_rows(rows)
            return
        summary = description.get("summary") or ""
        if not summary and not analysis:
            rows = [
                (
                    "AI 요약",
                    "<p class='empty'>아직 AI 분석 결과가 없습니다. "
                    "분석 큐에서 백그라운드 분석이 끝나면 이곳에 표시됩니다.</p>",
                )
            ]
            self._set_analysis_rows(rows)
            return
        rows: list[tuple[str, str]] = []
        if summary:
            rows.append(("AI 요약", bullets(summary_points(summary))))
        question = description.get("research_question") or ""
        if question:
            question_label = (
                "기술적 과제"
                if entry.metadata.document_type == "patent"
                else "검토 목적·범위"
                if entry.metadata.document_type == "review_paper"
                else "연구 질문"
            )
            rows.append((question_label, f"<p>{esc(question)}</p>"))
        field_labels = (
            (
                ("구현·실시예", "methods"),
                ("발명의 핵심", "contributions"),
                ("명시된 제약", "limitations"),
                ("키워드", "keywords"),
            )
            if entry.metadata.document_type == "patent"
            else (
                ("문헌 선정·종합 방법", "methods"),
                ("통합 결론", "contributions"),
                ("근거 한계·연구 공백", "limitations"),
                ("키워드", "keywords"),
            )
            if entry.metadata.document_type == "review_paper"
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
                rows.append((label, bullets(values)))
        patent_claims = str(analysis.get("patent_claims") or "").strip()
        if entry.metadata.document_type == "patent" and patent_claims:
            displayed_claims = _format_claims_for_display(patent_claims)
            rows.append(
                (
                    "청구항 원문",
                    "<div style='white-space:pre-wrap; overflow-wrap:anywhere; "
                    "line-height:1.5; font-family:monospace'>"
                    f"{esc(displayed_claims)}"
                    "</div>",
                )
            )
        classification = entry.record.get("classification", {})
        ai_tags = [str(value) for value in classification.get("ai_tags") or []]
        if ai_tags:
            rows.append(("AI 메타태그", f"<p>{esc(' · '.join(ai_tags))}</p>"))
        suggestion = str(analysis.get("suggested_category") or "").strip()
        if suggestion:
            rows.append(
                (
                    "추천 연구분야",
                    f"<p>{esc(suggestion)} — 연구분야 설정에 자동 반영됩니다.</p>",
                )
            )
        provenance = analysis.get("provenance") or entry.record.get(
            "provenance", {}
        ).get("summary")
        analysis_info_parts: list[str] = []
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
            analysis_info_parts.append(
                f"{esc(provenance.get('provider'))} / {esc(provenance.get('model'))}"
                f"{version_text}"
                f" · {esc(_format_analysis_time(analysis.get('completed_at', '')))}"
            )
        if analysis_info_parts:
            rows.append(
                (
                    "분석 정보",
                    "<p class='meta muted'>"
                    + "<br>".join(analysis_info_parts)
                    + "</p>",
                )
            )
        self._set_analysis_rows(rows)

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
        self._queue_reanalysis(entries, high=True)

    def _reanalyze_all(self) -> None:
        entries = self._controller.list_library()
        if not entries:
            return
        if QMessageBox.question(
            self,
            "전체 논문 재요약",
            f"라이브러리 전체 {len(entries)}건을 재요약 대기열에 넣을까요? "
            "수동 분석 모델과 수동 분석 간격으로 한 건씩 처리합니다.",
        ) != QMessageBox.Yes:
            return
        self._queue_reanalysis(entries, high=True)

    def _queue_reanalysis(
        self, entries: list[LibraryEntry], *, high: bool = False
    ) -> None:
        try:
            queued, problems = self._controller.queue_reanalysis(entries, high=high)
        except Exception as exc:
            QMessageBox.warning(self, "재요약 요청 실패", str(exc))
            return
        self.status_label.setText(
            f"수동 재요약 {queued}건을 분석 대기열에 넣었습니다."
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
            "추천 연구분야 적용",
            f"‘{suggestion}’을 연구분야 설정에 추가하고 이 논문을 다시 분석할까요?",
        ) != QMessageBox.Yes:
            return
        try:
            approved = self._controller.approve_category_suggestion(entry)
        except Exception as exc:
            QMessageBox.warning(self, "연구분야 적용 실패", str(exc))
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
        self.actions_changed.emit()

    def _save_selected(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("색인 편집 저장")
        dialog.setIcon(QMessageBox.Question)
        dialog.setText("색인 편집 내용을 저장할까요?")
        dialog.setInformativeText(
            "저장하면 PaperPack 메타데이터와 통합 색인을 갱신합니다. "
            "요약 내용까지 다시 만들 필요가 있으면 저장 후 재요약을 선택하세요."
        )
        save_only = dialog.addButton("저장만", QMessageBox.AcceptRole)
        save_and_reanalyze = dialog.addButton(
            "저장 후 재요약", QMessageBox.ActionRole
        )
        dialog.addButton(QMessageBox.Cancel)
        dialog.setDefaultButton(save_only)
        dialog.exec_()
        clicked = dialog.clickedButton()
        if clicked not in {save_only, save_and_reanalyze}:
            return
        try:
            updated = self._controller.update_library_metadata(entry, self.form.metadata())
        except Exception as exc:
            QMessageBox.warning(self, "색인 저장 실패", str(exc))
            return
        status = "PaperPack 메타데이터 저장 및 통합 인덱스 재생성을 완료했습니다."
        self.refresh()
        self.metadata_changed.emit()
        if clicked is save_and_reanalyze:
            self._queue_reanalysis([updated], high=True)
            status += " 재요약을 분석 대기열에 넣었습니다."
        self.status_label.setText(status)

    def _reverify_selected_bibliography(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        self.reverify_bibliography_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self._controller.reverify_library_bibliography(entry)
        except Exception as exc:
            QMessageBox.warning(self, "서지 재검증 실패", str(exc))
            self.reverify_bibliography_button.setEnabled(True)
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh(True)
        self.select_path(result.entry.sidecar_path)
        self.metadata_changed.emit()
        self.status_label.clear()

    def _show_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if index.isValid() and not self.table.item(index.row(), 0).isSelected():
            self.table.clearSelection()
            self.table.selectRow(index.row())
        entries = self._selected_entries()
        if not entries:
            return
        menu = QMenu(self)
        open_action = menu.addAction("PDF 열기")
        decorate_action(open_action, "open")
        open_action.setEnabled(self.open_button.isEnabled())
        open_action.triggered.connect(self._open_selected)
        open_with_ai_action = menu.addAction("AI 번역/요약과 함께 열기")
        decorate_action(open_with_ai_action, "ai")
        open_with_ai_action.setEnabled(self.open_with_ai_button.isEnabled())
        open_with_ai_action.triggered.connect(self._open_selected_with_ai)
        selection_ai_action = menu.addAction("선택 영역 창 다시 열기")
        decorate_action(selection_ai_action, "ai")
        selection_ai_action.setEnabled(self.selection_ai_button.isEnabled())
        selection_ai_action.triggered.connect(
            lambda: self._open_selection_ai_dialog(activate=True)
        )
        explorer_action = menu.addAction("탐색기에서 열기")
        decorate_action(explorer_action, "folder")
        explorer_action.triggered.connect(self._open_in_explorer)
        menu.addSeparator()
        apply_pdf_action = menu.addAction("편집본을 PaperPack에 적용")
        decorate_action(apply_pdf_action, "save")
        apply_pdf_action.setEnabled(self.apply_pdf_button.isEnabled())
        apply_pdf_action.triggered.connect(self._apply_pdf_edit)
        discard_pdf_action = menu.addAction("PDF 편집 취소")
        decorate_action(discard_pdf_action, "cancel")
        discard_pdf_action.setEnabled(self.discard_pdf_button.isEnabled())
        discard_pdf_action.triggered.connect(self._discard_pdf_edit)
        if len(entries) == 1:
            suggestion = str(
                entries[0].record.get("analysis", {}).get("suggested_category")
                or ""
            ).strip()
            approve_action = menu.addAction(
                f"AI 추천 연구분야 ‘{suggestion}’ 적용"
                if suggestion
                else "AI 추천 연구분야 없음"
            )
            decorate_action(approve_action, "check")
            approve_action.setEnabled(bool(suggestion))
            approve_action.triggered.connect(self._approve_category)
            if _should_show_bibliography_reverify(entries[0]):
                reverify_bib_action = menu.addAction("서지 재검증")
                decorate_action(reverify_bib_action, "refresh")
                reverify_bib_action.triggered.connect(
                    self._reverify_selected_bibliography
                )
        menu.addSeparator()
        reanalyze_action = menu.addAction("선택 논문 재요약")
        decorate_action(reanalyze_action, "refresh")
        reanalyze_action.setEnabled(self.reanalyze_selected_button.isEnabled())
        reanalyze_action.triggered.connect(self._reanalyze_selected)
        reanalyze_all_action = menu.addAction("전체 논문 재요약")
        decorate_action(reanalyze_all_action, "refresh")
        reanalyze_all_action.setEnabled(self.reanalyze_all_button.isEnabled())
        reanalyze_all_action.triggered.connect(self._reanalyze_all)
        menu.addSeparator()
        delete_action = menu.addAction("선택 항목을 앱 휴지통으로 이동…")
        decorate_action(delete_action, "delete")
        delete_action.setEnabled(self.delete_button.isEnabled())
        delete_action.triggered.connect(self._delete_selected)
        permanent_delete_action = menu.addAction("선택 항목 완전 삭제…")
        decorate_action(permanent_delete_action, "delete")
        permanent_delete_action.setEnabled(
            self.permanent_delete_button.isEnabled()
        )
        permanent_delete_action.triggered.connect(
            self._permanently_delete_library_selected
        )
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

    def _permanently_delete_library_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            return
        titles = "\n".join(
            f"• {entry.metadata.title or entry.sidecar_path.stem}"
            for entry in entries[:8]
        )
        if len(entries) > 8:
            titles += f"\n• 외 {len(entries) - 8}건"
        if QMessageBox.warning(
            self,
            "라이브러리 항목 완전 삭제",
            f"다음 {len(entries)}건의 PDF/PaperPack과 분석 내용을 완전히 삭제합니다.\n\n"
            f"{titles}\n\n"
            "앱 휴지통에 남지 않으며 복원할 수 없습니다. 감시 폴더의 별도 원본은 "
            "삭제하지 않습니다. 계속할까요?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Yes:
            return
        try:
            result = self._controller.permanently_delete_library_entries(entries)
        except Exception as exc:
            QMessageBox.warning(self, "완전 삭제 실패", str(exc))
            return
        self.refresh(True)
        self.status_label.setText(
            f"라이브러리 항목 {result.deleted}건을 완전히 삭제했습니다."
            + (f" · 확인 필요 {len(result.problems)}건" if result.problems else "")
        )
        if result.problems:
            QMessageBox.warning(
                self,
                "일부 항목 완전 삭제 확인 필요",
                "\n".join(result.problems[:10]),
            )
        if result.deleted:
            self.metadata_changed.emit()

    def _open_selected(self) -> None:
        failures: list[str] = []
        for entry in self._selected_entries():
            try:
                editable_pdf = self._controller.materialize_editable_pdf(entry.pdf_path)
                open_pdf(
                    editable_pdf,
                    self,
                    document_id=str(entry.record.get("id") or entry.work_id),
                )
            except Exception as exc:
                failures.append(f"{entry.metadata.title}: {exc}")
        self._refresh_pdf_edit_actions(self._selected_entries())
        if failures:
            QMessageBox.warning(self, "일부 PDF 열기 실패", "\n".join(failures[:10]))

    def _open_selected_with_ai(self) -> None:
        entries = self._selected_entries()
        if len(entries) != 1 or self._selection_ai is None:
            return
        entry = entries[0]
        try:
            editable_pdf = self._controller.materialize_editable_pdf(entry.pdf_path)
            self._spdf_selection = None
            spdf_window = open_pdf(
                editable_pdf,
                self,
                document_id=str(entry.record.get("id") or entry.work_id),
                selection_callback=self._spdf_selection_changed,
            )
            self._open_selection_ai_dialog(activate=False)
            self._focus_spdf_window(spdf_window)
            self._refresh_pdf_edit_actions(entries)
        except Exception as exc:
            QMessageBox.warning(self, "PDF 열기 실패", str(exc))

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
            open_pdf(
                editable_pdf,
                self,
                document_id=str(entry.record.get("id") or entry.work_id),
            )
            self._refresh_pdf_edit_actions(self._selected_entries())
        except Exception as exc:
            QMessageBox.warning(self, "PDF 열기 실패", str(exc))

    def _spdf_selection_changed(self, selection: SpdfSelection | None) -> None:
        self._spdf_selection = selection
        available = bool(
            selection is not None
            and selection.text.strip()
            and not selection.requires_ocr
            and self._selection_ai is not None
        )
        self.selection_ai_button.setEnabled(
            available and self._selection_worker is None
        )
        if self._selection_dialog is not None:
            self._selection_dialog.set_selection(
                selection,
                service_available=self._selection_ai is not None,
            )
            self._selection_dialog.set_busy(self._selection_worker is not None)
        if available and (
            self._selection_dialog is None
            or not self._selection_dialog.isVisible()
        ):
            self._open_selection_ai_dialog(activate=False)
            self._focus_spdf_window(active_spdf_window())

    def _open_selection_ai_dialog(self, *, activate: bool = True) -> None:
        if self._selection_dialog is None:
            dialog = SelectionAiDialog()
            dialog.action_requested.connect(self._run_selection_ai)
            dialog.cancel_requested.connect(
                lambda: self._selection_worker.cancel()
                if self._selection_worker
                else None
            )
            dialog.closed.connect(self._restore_tiled_spdf_window)
            self._selection_dialog = dialog
        self._selection_dialog.set_selection(
            self._spdf_selection,
            service_available=self._selection_ai is not None,
        )
        self._selection_dialog.show()
        self._position_selection_ai_dialog()
        self._selection_dialog.raise_()
        if activate:
            self._selection_dialog.activateWindow()

    @staticmethod
    def _focus_spdf_window(window) -> None:
        if window is None:
            return
        try:
            window.show()
            window.raise_()
            window.activateWindow()
        except RuntimeError:
            pass

    def close_selection_ai_dialog(self) -> None:
        if self._selection_dialog is not None:
            self._selection_dialog.close()

    def _position_selection_ai_dialog(self) -> None:
        dialog = self._selection_dialog
        spdf_window = active_spdf_window()
        if dialog is None or spdf_window is None:
            return
        try:
            spdf_frame = spdf_window.frameGeometry()
        except RuntimeError:
            return
        screen = QApplication.screenAt(spdf_frame.center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 10
        minimum_width = 380
        height = min(560, max(320, available.height() - margin * 2))
        top = max(available.top() + margin, min(spdf_frame.top(), available.bottom() - height))
        right_x = spdf_frame.right() + 1 + margin
        right_width = available.right() - right_x + 1
        left_width = spdf_frame.left() - available.left() - margin
        if right_width >= minimum_width:
            dialog.setGeometry(
                right_x,
                top,
                min(720, right_width),
                height,
            )
            return
        if left_width >= minimum_width:
            width = min(720, left_width)
            dialog.setGeometry(
                spdf_frame.left() - margin - width,
                top,
                width,
                height,
            )
            return

        if self._tiled_spdf_window is None:
            self._tiled_spdf_window = spdf_window
            self._tiled_spdf_geometry = spdf_window.geometry()
        dialog_width = max(minimum_width, int(available.width() * 0.4) - margin)
        spdf_width = available.width() - dialog_width - margin
        try:
            spdf_window.setGeometry(
                available.left(),
                available.top(),
                spdf_width,
                available.height(),
            )
        except RuntimeError:
            self._tiled_spdf_window = None
            self._tiled_spdf_geometry = None
            return
        dialog.setGeometry(
            available.left() + spdf_width + margin,
            available.top(),
            dialog_width,
            available.height(),
        )

    def _restore_tiled_spdf_window(self) -> None:
        window = self._tiled_spdf_window
        geometry = self._tiled_spdf_geometry
        self._tiled_spdf_window = None
        self._tiled_spdf_geometry = None
        if window is None or geometry is None:
            return
        try:
            window.setGeometry(geometry)
        except RuntimeError:
            pass

    def _run_selection_ai(self, action: str) -> None:
        if self._selection_ai is None or self._spdf_selection is None:
            return
        self._open_selection_ai_dialog()
        settings = self._controller.settings()
        allow_cloud_once = False
        if (
            settings.summary_provider in {"openai", "anthropic"}
            and not settings.cloud_processing_consent
        ):
            answer = QMessageBox.question(
                self,
                "선택 영역 클라우드 전송",
                f"선택한 {len(self._spdf_selection.text)}자만 {settings.summary_provider}로 전송합니다. 이번 한 번 허용할까요?",
            )
            if answer != QMessageBox.Yes:
                return
            allow_cloud_once = True
        self._selection_dialog.set_busy(True)
        self.selection_ai_button.setEnabled(False)
        worker = SelectionAiWorker(
            self._selection_ai,
            self._spdf_selection,
            action,
            allow_cloud_once,
            self,
        )
        worker.completed.connect(self._selection_ai_completed)
        worker.failed.connect(self._selection_ai_failed)
        worker.finished.connect(self._selection_ai_finished)
        self._selection_worker = worker
        worker.start()

    def _selection_ai_finished(self) -> None:
        self._selection_worker = None
        self._spdf_selection_changed(self._spdf_selection)

    def _selection_ai_completed(self, result) -> None:
        if self._selection_dialog is not None:
            self._selection_dialog.show_result(result.text)
            self._selection_dialog.set_selection(
                self._spdf_selection,
                service_available=self._selection_ai is not None,
            )
            self._selection_dialog.set_busy(False)
        self._spdf_selection_changed(self._spdf_selection)

    def _selection_ai_failed(self, message: str) -> None:
        if self._selection_dialog is not None:
            self._selection_dialog.set_selection(
                self._spdf_selection,
                service_available=self._selection_ai is not None,
            )
            self._selection_dialog.set_busy(False)
        self._spdf_selection_changed(self._spdf_selection)
        QMessageBox.warning(self, "선택 영역 AI 실패", message)

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
            "PDF 편집 취소",
            f"선택한 PaperPack {len(entries)}개의 원본은 유지하고 "
            "PDF 편집 작업본만 제거할까요?",
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
            f"PDF 편집 작업본 {removed}개를 제거했습니다."
            if removed
            else "제거할 PDF 편집 작업본이 없습니다."
        )
        if failures:
            QMessageBox.warning(
                self, "일부 PDF 편집 작업본 제거 실패", "\n".join(failures[:10])
            )
