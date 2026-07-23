"""Collection review and editable JSON library widgets."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event

from PyQt5.QtCore import QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QSpinBox,
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
from paper_organizer.application.analysis_queue import AnalysisQueueItem
from paper_organizer.application.background_analysis import (
    AnalysisRunEvent,
    BackgroundAnalysisService,
)
from paper_organizer.application.cloud_metadata_sync import MetadataConflict
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
            result = self._service.run_next()
            self._processing = False
            self.event.emit(result)
            if result.state in {"completed", "failed"}:
                self.queue_changed.emit()
            if result.state == "disabled":
                break
            self._wake.wait(self._service.poll_interval())
            self._wake.clear()
        self._processing = False


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
        self.summary_edit = QTextEdit()
        self.summary_edit.setMaximumHeight(105)
        form.addRow("제목", self.title_edit)
        form.addRow("저자", self.authors_edit)
        form.addRow("연도", self.year_edit)
        form.addRow("저널/학회", self.venue_edit)
        form.addRow("분야", self.category_edit)
        form.addRow("세부분야", self.subcategory_edit)
        form.addRow("태그", self.tags_edit)
        form.addRow("한국어 설명", self.summary_edit)

    def set_metadata(self, metadata: EditablePaperMetadata | None) -> None:
        value = metadata or EditablePaperMetadata()
        self.title_edit.setText(value.title)
        self.authors_edit.setText(", ".join(value.authors))
        self.year_edit.setText(str(value.year or ""))
        self.venue_edit.setText(value.venue)
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
            venue=self.venue_edit.text().strip(),
            category=self.category_edit.text().strip() or "Uncategorized",
            subcategory=self.subcategory_edit.text().strip() or "General",
            tags=split_values(self.tags_edit.text()),
            summary_ko=self.summary_edit.toPlainText().strip(),
        )


class CollectionReviewWidget(QWidget):
    library_changed = pyqtSignal()
    queue_changed = pyqtSignal()

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._items: list[ReviewItem] = []
        self._worker: _ScanWorker | None = None
        self._schedule_followup = False
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(lambda: self.scan_now(False))

        root = QVBoxLayout(self)
        paths = QGroupBox("폴더 및 저전력 감시")
        path_form = QFormLayout(paths)
        input_row, self.input_edit = self._path_row(self._browse_input)
        library_row, self.library_edit = self._path_row(self._browse_library)
        sync_row, self.sync_edit = self._path_row(self._browse_sync, allow_clear=True)
        self.profile_combo = QComboBox()
        self.profile_combo.addItem("저사양/절전", "eco")
        self.profile_combo.addItem("균형", "balanced")
        self.profile_combo.addItem("고성능", "performance")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 3600)
        self.interval_spin.setSuffix("초")
        self.interval_spin.setToolTip("5초에서 1시간 사이로 설정할 수 있습니다.")
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        self.interval_spin.valueChanged.connect(self._apply_timer_interval)
        self.auto_check = QCheckBox("설정한 주기로 가볍게 검색 (안정된 새 PDF만 1회 분석)")
        self.remove_source_check = QCheckBox(
            "paperpack 검증 완료 후 입력 폴더의 원본 PDF 삭제"
        )
        self.remove_source_check.setToolTip(
            "기본값은 원본 유지입니다. 삭제 실패 시 새 paperpack을 롤백합니다."
        )
        save_paths = QPushButton("폴더 설정 저장")
        save_paths.clicked.connect(self._save_paths)
        path_form.addRow("입력 폴더", input_row)
        path_form.addRow("PaperPack 라이브러리", library_row)
        path_form.addRow("OneDrive JSON 미러", sync_row)
        path_form.addRow("시스템 부하", self.profile_combo)
        path_form.addRow("스캔 주기", self.interval_spin)
        path_form.addRow("자동 감시", self.auto_check)
        path_form.addRow("입력 PDF", self.remove_source_check)
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
        self.organize_button = QPushButton("승인 후 paperpack으로 보관")
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
        profile_index = self.profile_combo.findData(settings.resource_profile)
        self.profile_combo.setCurrentIndex(max(0, profile_index))
        self.interval_spin.setValue(settings.scan_interval_seconds)
        self.auto_check.setChecked(settings.auto_enabled)
        self.remove_source_check.setChecked(settings.remove_source_after_import)
        self._set_auto_enabled(settings.auto_enabled)

    def _browse_input(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "입력 폴더 선택", self.input_edit.text())
        if path:
            self.input_edit.setText(path)

    def _browse_library(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "PaperPack 라이브러리 선택", self.library_edit.text()
        )
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
                resource_profile=self.profile_combo.currentData(),
                scan_interval_seconds=self.interval_spin.value(),
                remove_source_after_import=self.remove_source_check.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "폴더 설정 실패", str(exc))
            return
        self._set_auto_enabled(self.auto_check.isChecked())
        self.status_label.setText("폴더 설정을 저장했습니다.")

    def _set_auto_enabled(self, enabled: bool) -> None:
        self._apply_timer_interval()
        if enabled:
            self._auto_timer.start()
        else:
            self._auto_timer.stop()

    def _apply_timer_interval(self) -> None:
        if hasattr(self, "interval_spin"):
            self._auto_timer.setInterval(self.interval_spin.value() * 1000)

    def _profile_changed(self) -> None:
        defaults = {"eco": 300, "balanced": 60, "performance": 15}
        profile = self.profile_combo.currentData()
        if profile in defaults:
            self.interval_spin.setValue(defaults[profile])

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
        message = f"PaperPack 보관 완료: {result.pdf_path}"
        if result.sync_warning:
            message += f"\nJSON 미러 경고: {result.sync_warning}"
        QMessageBox.information(self, "논문 정리 완료", message)
        self.library_changed.emit()
        self.queue_changed.emit()
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


class AnalysisQueueWidget(QWidget):
    summary_requested = pyqtSignal(str)

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
        root = QVBoxLayout(self)
        note = QLabel(
            "새로 발견된 PDF가 재시작 후에도 유지되는 분석 대기열입니다. "
            "AI가 준비되지 않았으면 자동 호출하지 않고 여기에서 기다립니다."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["우선순위", "상태", "제목", "파일"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(self.table, 1)
        actions = QHBoxLayout()
        refresh_button = QPushButton("새로고침")
        self.priority_button = QPushButton("최우선으로 표시")
        self.summary_button = QPushButton("즉시 요약으로 보내기")
        self.remove_button = QPushButton("큐에서만 제거")
        self.run_now_button = QPushButton("선택 항목 지금 분석")
        self.background_button = QPushButton("백그라운드 분석 시작")
        refresh_button.clicked.connect(self.refresh)
        self.priority_button.clicked.connect(self._toggle_priority)
        self.summary_button.clicked.connect(self._send_to_summary)
        self.remove_button.clicked.connect(self._remove_selected)
        self.run_now_button.clicked.connect(self._run_selected_now)
        self.background_button.clicked.connect(self._toggle_background)
        actions.addWidget(refresh_button)
        actions.addWidget(self.priority_button)
        actions.addWidget(self.summary_button)
        actions.addWidget(self.remove_button)
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
        self.table.setRowCount(len(self._items))
        status_labels = {
            "pending_review": "검토 대기",
            "organized_pending_analysis": "정리됨 · 분석 대기",
            "analyzing": "분석 중",
            "completed": "완료",
            "failed": "실패",
        }
        for row, item in enumerate(self._items):
            values = [
                "높음" if item.priority else "보통",
                status_labels.get(item.status, item.status),
                item.title,
                item.path,
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.status_label.setText(f"분석 큐 {len(self._items)}개")
        self._update_background_button()
        self._selection_changed()

    def _selected(self) -> AnalysisQueueItem | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _selection_changed(self) -> None:
        item = self._selected()
        enabled = item is not None
        mutable = bool(item and item.status != "analyzing")
        self.priority_button.setEnabled(mutable)
        self.summary_button.setEnabled(bool(item and Path(item.path).is_file()))
        self.remove_button.setEnabled(mutable)
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
            "idle": "대기",
            "waiting": "AI 준비 대기",
            "completed": "완료",
            "failed": "실패",
            "disabled": "중지",
        }
        self.status_label.setText(f"{labels.get(event.state, event.state)} · {event.message}")

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
        self.search_edit.setPlaceholderText("제목, 저자, 연도, 분야, 태그, 설명 검색")
        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(lambda: self.refresh(True))
        self.search_edit.returnPressed.connect(self.refresh)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(refresh_button)
        root.addLayout(search_row)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["제목", "저널/학회", "저자", "연도", "분야", "판본"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._open_selected())
        root.addWidget(self.table, 1)
        self.form = MetadataForm("선택한 논문의 PaperPack 색인 편집")
        self.form.set_metadata(None)
        root.addWidget(self.form)
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
                metadata.venue,
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
        self.apply_pdf_button.setEnabled(False)
        self.discard_pdf_button.setEnabled(False)
        self.status_label.setText(f"논문 파일 {len(self._entries)}개")

    def _selected(self) -> LibraryEntry | None:
        row = self.table.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def _selection_changed(self) -> None:
        entry = self._selected()
        self.form.set_metadata(entry.metadata if entry else None)
        self.save_button.setEnabled(entry is not None)
        self.open_button.setEnabled(bool(entry and entry.pdf_path.is_file()))
        self._refresh_pdf_edit_actions(entry)

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
        if updated.sync_warning:
            status += f" OneDrive JSON 미러 경고: {updated.sync_warning}"
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
        if result.sync_warning:
            message += f" 경고: {result.sync_warning}"
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


class CloudSyncWidget(QWidget):
    metadata_changed = pyqtSignal()

    def __init__(self, controller: LibraryWorkflowController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._conflicts: list[MetadataConflict] = []
        root = QVBoxLayout(self)
        note = QLabel(
            "로컬 paperpack은 원본으로 보존하고, OneDrive의 portable-library.json은 "
            "클라우드 편집용으로 사용합니다. 양쪽이 모두 바뀐 경우에만 여기에서 선택합니다."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["논문", "충돌 유형", "설명"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(self.table, 1)
        comparison = QHBoxLayout()
        local_group = QGroupBox("로컬 원본")
        local_layout = QVBoxLayout(local_group)
        self.local_json = QTextEdit()
        self.local_json.setReadOnly(True)
        local_layout.addWidget(self.local_json)
        cloud_group = QGroupBox("클라우드 편집본")
        cloud_layout = QVBoxLayout(cloud_group)
        self.cloud_json = QTextEdit()
        self.cloud_json.setReadOnly(True)
        cloud_layout.addWidget(self.cloud_json)
        comparison.addWidget(local_group, 1)
        comparison.addWidget(cloud_group, 1)
        root.addLayout(comparison, 1)
        actions = QHBoxLayout()
        refresh_button = QPushButton("동기화 및 충돌 확인")
        self.local_button = QPushButton("로컬 원본 사용")
        self.cloud_button = QPushButton("클라우드 편집본 적용")
        refresh_button.clicked.connect(self.refresh)
        self.local_button.clicked.connect(lambda: self._resolve("local"))
        self.cloud_button.clicked.connect(lambda: self._resolve("cloud"))
        actions.addWidget(refresh_button)
        actions.addWidget(self.local_button)
        actions.addWidget(self.cloud_button)
        actions.addStretch(1)
        root.addLayout(actions)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        self._selection_changed()

    def refresh(self) -> None:
        try:
            self._conflicts = list(self._controller.metadata_conflicts())
            self.metadata_changed.emit()
        except Exception as exc:
            self._conflicts = []
            self.status_label.setText(f"동기화 확인 실패: {exc}")
        self.table.setRowCount(len(self._conflicts))
        for row, conflict in enumerate(self._conflicts):
            values = [conflict.title, conflict.kind, conflict.message]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if self._conflicts:
            self.status_label.setText(
                f"자동 병합하지 않은 충돌 {len(self._conflicts)}개 · 항목을 선택해 사용할 값을 결정하세요."
            )
        else:
            settings = self._controller.settings()
            self.status_label.setText(
                "충돌이 없습니다."
                if settings.metadata_sync_dir
                else "수집 및 검토 화면에서 OneDrive JSON 미러 폴더를 먼저 지정하세요."
            )
        self._selection_changed()

    def _selected(self) -> MetadataConflict | None:
        row = self.table.currentRow()
        return self._conflicts[row] if 0 <= row < len(self._conflicts) else None

    def _selection_changed(self) -> None:
        conflict = self._selected()
        self.local_button.setEnabled(conflict is not None)
        self.cloud_button.setEnabled(bool(conflict and conflict.can_use_cloud))
        if conflict is None:
            self.local_json.clear()
            self.cloud_json.clear()
            return
        dump = lambda value: json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True
        ) if value is not None else "(없음)"
        self.local_json.setPlainText(dump(conflict.local_record))
        self.cloud_json.setPlainText(dump(conflict.cloud_record))
        self.local_button.setText(
            "클라우드 고아 항목 제거"
            if conflict.local_record is None
            else "로컬 원본 사용"
        )

    def _resolve(self, choice: str) -> None:
        conflict = self._selected()
        if conflict is None:
            return
        label = "로컬 원본" if choice == "local" else "클라우드 편집본"
        if QMessageBox.question(
            self,
            "동기화 충돌 해결",
            f"'{conflict.title}'에 {label}을(를) 사용해 충돌을 해결할까요? "
            "클라우드 값을 적용하면 현재 로컬 JSON은 이력에 보관됩니다.",
        ) != QMessageBox.Yes:
            return
        try:
            self._controller.resolve_metadata_conflict(conflict.record_id, choice)
        except Exception as exc:
            QMessageBox.warning(self, "충돌 해결 실패", str(exc))
            return
        self.refresh()
