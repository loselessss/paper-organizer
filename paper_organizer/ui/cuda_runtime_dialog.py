"""Offer an explicit, cancellable download of the optional CUDA runtime."""

from threading import Event

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout

from paper_organizer.application.cuda_runtime_manager import CudaRuntimeManager
from paper_organizer.ui.dialog_utils import suppress_context_help_button


class _CudaDownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    completed = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.cancel = Event()

    def run(self):
        try:
            self.manager.install(cancel=self.cancel, on_progress=self.progress.emit)
            self.completed.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class CudaRuntimeDialog(QDialog):
    def __init__(self, parent=None, *, manager=None):
        super().__init__(parent)
        suppress_context_help_button(self)
        self.setWindowTitle("CUDA 가속 선택 설치")
        self.resize(540, 250)
        self._manager = manager or CudaRuntimeManager()
        self._worker = None
        layout = QVBoxLayout(self)
        note = QLabel(
            "NVIDIA GPU가 있고 더 빠른 AI 분석이 필요할 때 CUDA 12.4 런타임을 설치하세요.\n"
            "약 642MB를 공식 llama.cpp 배포처에서 내려받습니다. 설치·임시 파일용 여유 공간이 필요합니다.\n"
            "NVIDIA 드라이버는 포함하지 않습니다. 호환 드라이버가 없거나 초기화에 실패하면 Vulkan·CPU로 실행됩니다.\n"
            "설치하지 않아도 기본 Vulkan GPU·CPU 분석을 사용할 수 있습니다.\n"
            "CUDA 구성 요소에는 NVIDIA CUDA 라이선스가 적용됩니다."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        license_link = QLabel('<a href="https://docs.nvidia.com/cuda/archive/12.4.1/eula/index.html">NVIDIA CUDA 라이선스 보기</a>')
        license_link.setOpenExternalLinks(True)
        layout.addWidget(license_link)
        self.status = QLabel("설치됨" if self._manager.installed() else "선택 설치 · 자동으로 다운로드하지 않습니다.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.download_button = QPushButton("동의하고 CUDA 다운로드")
        self.download_button.setEnabled(not self._manager.installed())
        self.download_button.clicked.connect(self._download)
        layout.addWidget(self.download_button)
        self.close_button = QPushButton("닫기")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)

    def _download(self):
        if self._worker is not None:
            return
        self.download_button.setEnabled(False)
        self.close_button.setText("다운로드 취소")
        self.status.setText("CUDA 런타임 다운로드 중…")
        worker = _CudaDownloadWorker(self._manager, self)
        worker.progress.connect(self._progress)
        worker.completed.connect(self._completed)
        worker.failed.connect(self.status.setText)
        worker.finished.connect(self._finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _progress(self, received, total):
        self.progress.setValue(min(100, int(received * 100 / max(1, total))))
        self.status.setText(f"CUDA 다운로드 {received / 1_000_000:.1f}/{total / 1_000_000:.1f}MB · 완료 후 파일 검증")

    def _completed(self):
        self.status.setText("CUDA 설치 완료 · 다음 앱 실행부터 CUDA를 우선 사용합니다.")
        self.progress.setValue(100)

    def _finished(self):
        self._worker = None
        self.close_button.setText("닫기")
        self.download_button.setEnabled(not self._manager.installed())

    def reject(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel.set()
            self.status.setText("다운로드 취소 및 임시 파일 정리 중…")
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
