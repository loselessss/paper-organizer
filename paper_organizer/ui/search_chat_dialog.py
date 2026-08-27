"""Natural-language library search dialog with evidence-linked results."""

from __future__ import annotations

import html

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
)

from paper_organizer.application.conversational_search import (
    ConversationalSearchController,
    ConversationalSearchResult,
    PreparedSearch,
)
from paper_organizer.ui.dialog_utils import suppress_context_help_button
from paper_organizer.ui.fluent_style import decorate_button


class _PrepareSearchWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        controller: ConversationalSearchController,
        question: str,
        allow_cloud_once: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._question = question
        self._allow_cloud_once = allow_cloud_once

    def run(self) -> None:
        try:
            self.completed.emit(
                self._controller.prepare(
                    self._question,
                    allow_cloud_once=self._allow_cloud_once,
                )
            )
        except Exception as exc:
            self.failed.emit(" ".join(str(exc).split()) or exc.__class__.__name__)


class _AnswerSearchWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        controller: ConversationalSearchController,
        prepared: PreparedSearch,
        allow_cloud_once: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._prepared = prepared
        self._allow_cloud_once = allow_cloud_once

    def run(self) -> None:
        try:
            self.completed.emit(
                self._controller.answer(
                    self._prepared,
                    allow_cloud_once=self._allow_cloud_once,
                )
            )
        except Exception as exc:
            self.failed.emit(" ".join(str(exc).split()) or exc.__class__.__name__)


class SearchChatDialog(QDialog):
    paper_requested = pyqtSignal(str)

    def __init__(
        self, controller: ConversationalSearchController, parent=None
    ) -> None:
        super().__init__(parent)
        suppress_context_help_button(self)
        self._controller = controller
        self._prepared: PreparedSearch | None = None
        self._worker: QThread | None = None
        self._auto_answer_after_prepare = False
        self._stop_requested = False
        self.setWindowTitle("자연어로 논문 찾기")
        self.resize(980, 720)

        root = QVBoxLayout(self)
        intro = QLabel(
            "질문을 AI가 검색어로 바꾼 뒤 SQLite 전문 검색으로 후보를 좁히고, "
            "후보 논문의 실제 페이지 본문만 근거로 답합니다."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        view = controller.provider_view()
        self.provider_label = QLabel(f"AI: {view.provider} / {view.model}")
        root.addWidget(self.provider_label)

        self.question_edit = QTextEdit()
        self.question_edit.setPlaceholderText(
            "예: 2022년 이후 열안정성 효소를 만든 연구와 사용한 방법을 찾아줘"
        )
        self.question_edit.setFixedHeight(84)
        self.question_edit.textChanged.connect(self._question_changed)
        root.addWidget(self.question_edit)

        action_row = QHBoxLayout()
        self.search_button = QPushButton("후보 논문 찾기")
        self.answer_button = QPushButton("근거 본문으로 답변 생성")
        self.stop_button = QPushButton("정지")
        self.answer_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        decorate_button(self.stop_button, "stop", role="destructive")
        self.search_button.clicked.connect(self._prepare)
        self.answer_button.clicked.connect(self._answer)
        self.stop_button.clicked.connect(self._stop_search)
        action_row.addWidget(self.search_button)
        action_row.addWidget(self.answer_button)
        action_row.addWidget(self.stop_button)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self.preview_label = QLabel("질문을 입력한 뒤 후보 논문 찾기를 누르세요.")
        self.preview_label.setWordWrap(True)
        root.addWidget(self.preview_label)

        self.answer_view = QTextBrowser()
        self.answer_view.setPlaceholderText("AI 답변이 여기에 표시됩니다.")
        root.addWidget(self.answer_view, 2)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["논문", "연도", "근거 페이지", "분야", "선정 이유"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self._open_row)
        root.addWidget(self.table, 2)

        self.status_label = QLabel()
        root.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def set_question(self, question: str) -> None:
        self.question_edit.setPlainText(question)
        self.question_edit.moveCursor(QTextCursor.End)

    def start_search(self) -> None:
        self._prepare()

    def _question_changed(self) -> None:
        if self._worker is not None:
            return
        self._prepared = None
        self._auto_answer_after_prepare = False
        self.answer_button.setEnabled(False)
        self.answer_view.clear()
        self.table.setRowCount(0)

    def _prepare(self) -> None:
        question = " ".join(self.question_edit.toPlainText().split())
        if not question:
            QMessageBox.information(self, "질문 필요", "자연어 질문을 입력하세요.")
            return
        view = self._controller.provider_view()
        allow_cloud_once = False
        if view.requires_cloud_consent:
            if QMessageBox.question(
                self,
                "질문 전송 동의",
                f"{view.provider}에 질문을 보내 검색어를 준비합니다. "
                "이 단계에서는 논문 본문을 보내지 않습니다. 계속할까요?",
            ) != QMessageBox.Yes:
                return
            allow_cloud_once = True
        self._stop_requested = False
        self._set_busy(True, "질문을 해석하고 로컬 전문 검색을 실행하고 있습니다…")
        worker = _PrepareSearchWorker(
            self._controller,
            question,
            allow_cloud_once,
            self,
        )
        worker.completed.connect(self._prepared_ready)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _prepared_ready(self, prepared: PreparedSearch) -> None:
        if self._stop_requested:
            return
        self._prepared = prepared
        self._auto_answer_after_prepare = False
        self._render_candidates(prepared)
        if not prepared.candidates:
            self.preview_label.setText(
                "질문과 연결되는 본문 후보를 찾지 못했습니다. "
                "기술명이나 핵심 용어를 조금 더 구체적으로 적어보세요."
            )
            self.answer_button.setEnabled(False)
            self.status_label.setText("후보 없음 · 답변 AI는 호출하지 않았습니다.")
            return
        destination = (
            f"{prepared.provider} 전송 대기"
            if prepared.sends_to_cloud
            else "내장 로컬 AI 처리"
        )
        context = (
            f" · 컨텍스트 {prepared.context_window:,}토큰"
            if prepared.context_window is not None
            else ""
        )
        queries = ", ".join(prepared.plan.search_queries)
        self.preview_label.setText(
            f"{destination}: 후보 {len(prepared.candidates)}편, "
            f"본문·메타데이터 {prepared.character_count:,}자{context}\n"
            f"검색어: {queries}"
        )
        self._auto_answer_after_prepare = not prepared.requires_cloud_consent
        self.answer_button.setEnabled(False)
        self.status_label.setText(
            "후보 준비 완료 · 아직 후보 논문 본문은 AI에 보내지 않았습니다."
            if prepared.sends_to_cloud and prepared.requires_cloud_consent
            else "후보 준비 완료 · 근거 답변을 자동으로 생성합니다."
        )

    def _answer(self) -> None:
        prepared = self._prepared
        if prepared is None or not prepared.candidates:
            return
        allow_cloud_once = False
        if prepared.sends_to_cloud and prepared.requires_cloud_consent:
            if QMessageBox.question(
                self,
                "후보 본문 전송 동의",
                f"후보 논문 {len(prepared.candidates)}편의 본문·메타데이터 "
                f"{prepared.character_count:,}자를 {prepared.provider}에 이번 한 번 "
                "전송해 답변을 생성할까요?",
            ) != QMessageBox.Yes:
                return
            allow_cloud_once = True
        self._stop_requested = False
        self._set_busy(True, "후보 본문에서 근거를 확인하고 있습니다…")
        worker = _AnswerSearchWorker(
            self._controller,
            prepared,
            allow_cloud_once,
            self,
        )
        worker.completed.connect(self._answer_ready)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _answer_ready(self, result: ConversationalSearchResult) -> None:
        if self._stop_requested:
            return
        confidence = {"high": "높음", "medium": "보통", "low": "낮음"}.get(
            result.answer.confidence,
            result.answer.confidence,
        )
        self.answer_view.setHtml(
            f"<h3>답변</h3><p>{html.escape(result.answer.answer_ko)}</p>"
            f"<p style='color:#777'>신뢰도 {html.escape(confidence)} · "
            f"{html.escape(result.provider)} / {html.escape(result.model)}</p>"
        )
        reasons = {
            evidence.file_id: (
                ", ".join(str(page) for page in evidence.pages),
                evidence.why,
            )
            for evidence in result.answer.papers
        }
        for row, candidate in enumerate(result.candidates):
            pages, why = reasons.get(candidate.file_id, ("", ""))
            self.table.item(row, 2).setText(pages)
            self.table.item(row, 4).setText(why)
        self.status_label.setText(
            f"답변 완료 · 근거 논문 {len(result.answer.papers)}편"
        )

    def _render_candidates(self, prepared: PreparedSearch) -> None:
        self.table.setRowCount(len(prepared.candidates))
        for row, candidate in enumerate(prepared.candidates):
            values = [
                candidate.title,
                str(candidate.year or ""),
                ", ".join(str(page) for page in candidate.pages),
                candidate.category,
                "",
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, str(candidate.sidecar_path))
                self.table.setItem(row, column, cell)

    def _open_row(self, row: int, _column: int) -> None:
        cell = self.table.item(row, 0)
        path = cell.data(Qt.UserRole) if cell is not None else ""
        if path:
            self._controller.stop_local_runtime()
            self.paper_requested.emit(str(path))
            self.accept()

    def _failed(self, message: str) -> None:
        self._controller.stop_local_runtime()
        if self._stop_requested:
            self.status_label.setText("자연어 검색을 정지했습니다.")
            return
        self.status_label.setText(f"자연어 검색 실패: {message}")
        QMessageBox.warning(self, "자연어 검색 실패", message)

    def _stop_search(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._stop_requested = True
        self._auto_answer_after_prepare = False
        worker.requestInterruption()
        self._controller.stop_local_runtime()
        self.stop_button.setEnabled(False)
        self.status_label.setText("자연어 검색 정지를 요청했습니다…")

    def _set_busy(self, busy: bool, message: str) -> None:
        self.search_button.setEnabled(not busy)
        self.answer_button.setEnabled(
            not busy
            and self._prepared is not None
            and bool(self._prepared.candidates)
        )
        self.stop_button.setEnabled(busy)
        self.question_edit.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.status_label.setText(message)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        stopped = self._stop_requested
        self._stop_requested = False
        self._set_busy(False, self.status_label.text())
        if stopped:
            self.status_label.setText("자연어 검색을 정지했습니다.")
            return
        if self._auto_answer_after_prepare:
            self._auto_answer_after_prepare = False
            QTimer.singleShot(0, self._answer)

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self,
                "검색 진행 중",
                "현재 검색 또는 답변 생성이 끝난 뒤 창을 닫으세요.",
            )
            event.ignore()
            return
        self._controller.stop_local_runtime()
        super().closeEvent(event)

    def reject(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(
                self,
                "검색 진행 중",
                "현재 검색 또는 답변 생성이 끝난 뒤 창을 닫을 수 있습니다.",
            )
            return
        self._controller.stop_local_runtime()
        super().reject()
