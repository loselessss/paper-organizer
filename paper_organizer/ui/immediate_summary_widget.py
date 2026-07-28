"""Immediate summary widget with mandatory preview and background execution."""

from __future__ import annotations

import html
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from paper_organizer.application.summary_service import (
    ImmediateSummaryController,
    PreparedSummary,
    SummaryExecution,
    SummaryMode,
)


class _PrepareWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, controller, path, mode, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._path = path
        self._mode = mode

    def run(self):
        try:
            self.completed.emit(self._controller.prepare(self._path, self._mode))
        except Exception as exc:
            self.failed.emit(str(exc))


class _SummaryWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, controller, prepared, allow_cloud_once, parent=None):
        super().__init__(parent)
        self._controller = controller
        self._prepared = prepared
        self._allow_cloud_once = allow_cloud_once

    def run(self):
        try:
            self.completed.emit(
                self._controller.run(
                    self._prepared, allow_cloud_once=self._allow_cloud_once
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            from paper_organizer.infra.ollama_installer import stop_managed_runtime

            stop_managed_runtime()


class ImmediateSummaryWidget(QWidget):
    def __init__(self, controller: ImmediateSummaryController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._prepared: PreparedSummary | None = None
        self._worker: QThread | None = None

        root = QVBoxLayout(self)
        title = QLabel("즉시 요약")
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        root.addWidget(title)

        file_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("요약할 PDF 파일")
        browse_button = QPushButton("찾아보기...")
        browse_button.clicked.connect(self._browse)
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(browse_button)
        root.addLayout(file_row)

        action_row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("빠른 요약", SummaryMode.QUICK.value)
        self.mode_combo.addItem("정밀 분석", SummaryMode.FULL.value)
        self.preview_button = QPushButton("전송 미리보기")
        self.preview_button.clicked.connect(self._prepare)
        self.run_button = QPushButton("요약 실행")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self._run)
        action_row.addWidget(self.mode_combo)
        action_row.addWidget(self.preview_button)
        action_row.addWidget(self.run_button)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self.preview_label = QLabel("PDF와 모드를 선택한 뒤 전송 미리보기를 확인하세요.")
        self.preview_label.setWordWrap(True)
        root.addWidget(self.preview_label)
        self.consent_check = QCheckBox("이번 요청의 클라우드 전송을 허용")
        self.consent_check.setVisible(False)
        root.addWidget(self.consent_check)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #555;")
        root.addWidget(self.status_label)
        self.output = QTextBrowser()
        self.output.setHtml("<p style='color:#777'>아직 생성된 임시 요약이 없습니다.</p>")
        root.addWidget(self.output, 1)

        self.path_edit.textChanged.connect(self._invalidate_preview)
        self.mode_combo.currentIndexChanged.connect(self._invalidate_preview)

    def _browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "요약할 PDF 선택", self.path_edit.text(), "PDF files (*.pdf)"
        )
        if path:
            self.path_edit.setText(path)
            QTimer.singleShot(0, self._prepare)
            QTimer.singleShot(0, self._prepare)

    def _invalidate_preview(self) -> None:
        self._prepared = None
        self.run_button.setEnabled(False)
        self.consent_check.setVisible(False)

    def _prepare(self) -> None:
        path = Path(self.path_edit.text().strip())
        if not self.path_edit.text().strip():
            QMessageBox.information(self, "PDF 필요", "요약할 PDF를 선택하세요.")
            return
        self._prepared = None
        self.run_button.setEnabled(False)
        self._set_busy(True, "PDF에서 전송 범위를 준비하고 있습니다...")
        worker = _PrepareWorker(
            self._controller, path, self.mode_combo.currentData(), self
        )
        worker.completed.connect(self._prepared_ready)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _prepared_ready(self, prepared: PreparedSummary) -> None:
        self._prepared = prepared
        preview = prepared.preview
        destination = "클라우드 전송" if preview.sends_to_cloud else "로컬 처리"
        truncated = " · 길이 제한으로 일부 생략" if preview.truncated else ""
        context = (
            f" · 컨텍스트 {preview.context_window:,}토큰"
            if preview.context_window is not None
            else ""
        )
        sections = " · ".join(preview.included_sections) or "문서 본문"
        language = (
            "한국어 번역"
            if preview.output_language == "ko"
            else "원문 언어 유지"
        )
        strategy = (
            "구역별 요약 → 전체 요약 (기여·한계 제외)"
            if preview.summary_strategy == "hierarchical"
            else "전체 구역 통합 요약"
        )
        self.preview_label.setText(
            f"{destination}: {preview.provider} / {preview.model}\n"
            f"PDF {len(preview.included_pdf_pages)}쪽, {preview.character_count:,}자, "
            f"입력 약 {preview.estimated_input_tokens:,}토큰{context}{truncated}\n"
            f"대상 페이지: {', '.join(map(str, preview.included_pdf_pages))}\n"
            f"구역: {sections} · 출력: {language}\n방식: {strategy}"
        )
        self.consent_check.setVisible(preview.sends_to_cloud)
        self.consent_check.setEnabled(preview.requires_cloud_consent)
        self.consent_check.setChecked(not preview.requires_cloud_consent)
        self.run_button.setEnabled(True)
        self.preview_button.hide()
        self.status_label.setText("미리보기 완료 · 실행 전까지 전송되지 않습니다.")

    def _run(self) -> None:
        if self._prepared is None:
            return
        preview = self._prepared.preview
        if (
            preview.requires_cloud_consent
            and not self.consent_check.isChecked()
        ):
            QMessageBox.warning(
                self, "전송 동의 필요", "이번 클라우드 전송에 동의해야 실행할 수 있습니다."
            )
            return
        self._set_busy(True, "요약 AI가 임시 분석을 생성하고 있습니다...")
        worker = _SummaryWorker(
            self._controller,
            self._prepared,
            self.consent_check.isChecked(),
            self,
        )
        worker.completed.connect(self._summary_ready)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        self._worker = worker
        worker.start()

    def _summary_ready(self, execution: SummaryExecution) -> None:
        data = execution.result.data
        esc = lambda value: html.escape(str(value or ""))
        bullets = lambda values: "".join(
            f"<li>{esc(value)}</li>" for value in values
        )
        paragraphs = "".join(
            f"<p>{esc(value)}</p>"
            for value in data.summary_ko.split("\n\n")
            if value.strip()
        )
        self.output.setHtml(
            "<p><b>임시 분석</b> — 검토 전에는 파일 이동·정식 색인에 사용되지 않습니다.</p>"
            f"<h3>요약</h3>{paragraphs}"
            f"<h3>연구 질문</h3><p>{esc(data.research_question)}</p>"
            f"<h3>방법</h3><ul>{bullets(data.methods)}</ul>"
            f"<h3>핵심 기여</h3><ul>{bullets(data.contributions)}</ul>"
            f"<h3>한계</h3><ul>{bullets(data.limitations)}</ul>"
            f"<p style='color:#777'>{esc(execution.result.provider)} / "
            f"{esc(execution.result.model)}</p>"
        )
        self.status_label.setText("임시 요약 완료")

    def _failed(self, message: str) -> None:
        self.status_label.setText(f"실패: {message}")
        QMessageBox.warning(self, "즉시 요약 실패", message)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    def _set_busy(self, busy: bool, status: str = "") -> None:
        self.preview_button.setEnabled(not busy)
        self.run_button.setEnabled(not busy and self._prepared is not None)
        self.mode_combo.setEnabled(not busy)
        self.path_edit.setEnabled(not busy)
        if status:
            self.status_label.setText(status)

    def is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def select_pdf(self, path: str | Path) -> None:
        """Load a queued paper and prepare its non-transmitting preview."""
        self.path_edit.setText(str(path))
        quick_index = self.mode_combo.findData(SummaryMode.QUICK.value)
        if quick_index >= 0:
            self.mode_combo.setCurrentIndex(quick_index)
        self.setFocus()
        QTimer.singleShot(0, self._prepare)


class ImmediateSummaryDialog(QDialog):
    """분석 큐나 메뉴에서 여는 즉시 요약 다이얼로그(임시 분석 전용)."""

    def __init__(self, controller: ImmediateSummaryController, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowTitle("즉시 요약")
        self.setMinimumSize(680, 560)
        layout = QVBoxLayout(self)
        self.widget = ImmediateSummaryWidget(controller, self)
        layout.addWidget(self.widget)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowContextHelpButtonHint
        )

    def select_pdf(self, path) -> None:
        self.widget.select_pdf(path)

    def reject(self) -> None:
        if self.widget.is_busy():
            QMessageBox.information(
                self, "요약 진행 중", "현재 요청이 끝난 뒤 창을 닫으세요."
            )
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self.widget.is_busy():
            event.ignore()
            return
        super().closeEvent(event)
