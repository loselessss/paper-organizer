"""Install optional, hash-pinned CUDA runtime files only on user request."""

from pathlib import Path
from threading import Event

from paper_organizer.infra import llama_bundle
from paper_organizer.infra.settings import default_settings_path


CUDA_DOWNLOAD_BYTES = 250_587_736 + 391_443_627


def cuda_runtime_dir() -> Path:
    return default_settings_path().parent / "runtimes" / f"{llama_bundle.VERSION}-cuda"


class CudaRuntimeManager:
    def __init__(self, directory: Path | None = None, *, opener=None):
        self.directory = directory or cuda_runtime_dir()
        self._opener = opener

    def installed(self) -> bool:
        return all((self.directory / name).is_file() for name in (
            "llama-server.exe", "ggml-cuda.dll", "cudart64_12.dll",
            "cublas64_12.dll", "cublasLt64_12.dll", "runtime-manifest.json",
        ))

    def install(self, *, cancel: Event | None = None, on_progress=None) -> Path:
        if self.installed():
            llama_bundle.validate_bundle(self.directory, backend="cuda")
            return self.directory
        cache = self.directory.parent / "downloads"
        completed = 0
        archives = []
        for backend in ("cuda", "cudart"):
            if cancel is not None and cancel.is_set():
                raise RuntimeError("CUDA 다운로드를 취소했습니다.")
            name, _url, expected = llama_bundle.asset(backend)
            archive = cache / name
            if not archive.is_file():
                llama_bundle.download_archive(
                    archive, backend=backend, cancel=cancel, opener=self._opener,
                    on_progress=(lambda received: on_progress(completed + received, CUDA_DOWNLOAD_BYTES)) if on_progress else None,
                )
            if llama_bundle.digest(archive) != expected:
                raise ValueError("CUDA 다운로드 파일의 SHA-256 검증에 실패했습니다.")
            completed += archive.stat().st_size
            archives.append(archive)
            if on_progress:
                on_progress(completed, CUDA_DOWNLOAD_BYTES)
        if cancel is not None and cancel.is_set():
            raise RuntimeError("CUDA 다운로드를 취소했습니다.")
        return llama_bundle.prepare_bundle(
            archives[0], self.directory, backend="cuda", cudart_archive=archives[1],
        )
