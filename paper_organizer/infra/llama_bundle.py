"""Prepare and verify pinned Windows x64 CUDA, Vulkan and CPU bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen
import zipfile
from threading import Event


ROOT = Path(__file__).resolve().parents[2]
VERSION = "b10715"
ARCHIVE_NAME = f"llama-{VERSION}-bin-win-cpu-x64.zip"
URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/{ARCHIVE_NAME}"
SHA256 = "b01268e9d933477f5c12a0b89d1ecde69e4b914ec4a1e0ce52ed1cfcf563d19e"
BUNDLE_DIR = ROOT / "build" / "llama-runtime" / VERSION
VULKAN_ARCHIVE_NAME = f"llama-{VERSION}-bin-win-vulkan-x64.zip"
VULKAN_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/{VULKAN_ARCHIVE_NAME}"
VULKAN_SHA256 = "edb1b8bec2558fdafac2769472b5dd98f99220b41a0ff4ebabae364dcd3fb6d5"
VULKAN_DIR = BUNDLE_DIR.with_name(f"{VERSION}-vulkan")
CUDA_ARCHIVE_NAME = f"llama-{VERSION}-bin-win-cuda-12.4-x64.zip"
CUDA_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/{CUDA_ARCHIVE_NAME}"
CUDA_SHA256 = "5afae45af8df77039a586f333f4da7824cf687245164c57b84f7fec00e35b2bf"
CUDA_DIR = BUNDLE_DIR.with_name(f"{VERSION}-cuda")
CUDART_ARCHIVE_NAME = "cudart-llama-bin-win-cuda-12.4-x64.zip"
CUDART_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/{CUDART_ARCHIVE_NAME}"
CUDART_SHA256 = "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"
BACKENDS = ("cpu", "vulkan", "cuda")
CUDA_NOTICE = Path(__file__).resolve().parents[1] / "assets" / "licenses" / "NVIDIA-CUDA-NOTICE.txt"
LICENSE = Path(__file__).resolve().parents[1] / "assets" / "licenses" / "llama.cpp-LICENSE.txt"
REQUIRED = {
    "llama-server.exe", "llama-server-impl.dll", "llama.dll", "llama-common.dll",
    "ggml.dll", "ggml-base.dll", "ggml-cpu-x64.dll", "mtmd.dll", "libomp.dll",
    "LICENSE-LLVM-OpenMP", "llama.cpp-LICENSE.txt",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_name(name: str) -> bool:
    return bool(name) and name not in {".", ".."} and not any(
        character in name for character in ("/", "\\", ":")
    )


def asset(backend: str) -> tuple[str, str, str]:
    return {
        "cpu": (ARCHIVE_NAME, URL, SHA256),
        "vulkan": (VULKAN_ARCHIVE_NAME, VULKAN_URL, VULKAN_SHA256),
        "cuda": (CUDA_ARCHIVE_NAME, CUDA_URL, CUDA_SHA256),
        "cudart": (CUDART_ARCHIVE_NAME, CUDART_URL, CUDART_SHA256),
    }[backend]


def download_archive(path: Path, *, backend: str = "cpu", cancel: Event | None = None, on_progress=None, opener=None) -> None:
    """Download atomically and reject any bytes other than the pinned archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="llama-download-", dir=path.parent) as temp:
        partial = Path(temp) / ARCHIVE_NAME
        _name, url, expected_sha = asset(backend)
        received = 0
        with (opener or urlopen)(url, timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("CUDA 다운로드를 취소했습니다.")
                output.write(chunk)
                received += len(chunk)
                if on_progress is not None:
                    on_progress(received)
        if digest(partial) != expected_sha:
            raise ValueError("내장 AI 런타임 다운로드의 SHA-256이 일치하지 않습니다.")
        os.replace(partial, path)


def validate_bundle(directory: Path, *, backend: str = "cpu") -> None:
    """Fail closed on missing, stale, altered, or incomplete runtime files."""
    manifest = json.loads((directory / "runtime-manifest.json").read_text(encoding="utf-8"))
    expected_sha = asset(backend)[2]
    if manifest.get("version") != VERSION or manifest.get("archive_sha256") != expected_sha:
        raise ValueError("내장 AI 런타임 버전 또는 원본 해시가 일치하지 않습니다.")
    files = manifest.get("files", {})
    required = REQUIRED.copy()
    if backend == "vulkan":
        required.add("ggml-vulkan.dll")
    elif backend == "cuda":
        required.update({"ggml-cuda.dll", "cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll", CUDA_NOTICE.name})
        if manifest.get("cudart_sha256") != CUDART_SHA256:
            raise ValueError("CUDA 의존 DLL 원본 해시가 일치하지 않습니다.")
    if not isinstance(files, dict) or not required <= files.keys():
        raise ValueError("내장 AI 런타임의 필수 DLL 또는 라이선스가 누락되었습니다.")
    actual = {path.name for path in directory.iterdir()} - {"runtime-manifest.json"}
    if actual != set(files):
        raise ValueError("내장 AI 런타임의 파일 목록이 일치하지 않습니다.")
    for name, expected in files.items():
        if not safe_name(name):
            raise ValueError("내장 AI 런타임 파일 경로가 올바르지 않습니다.")
        path = directory / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError(f"내장 AI 런타임 파일 검증 실패: {name}")


def prepare_bundle(archive: Path, directory: Path = BUNDLE_DIR, *, backend: str = "cpu", cudart_archive: Path | None = None) -> Path:
    """Stage the server, every companion DLL and licenses before publishing."""
    expected_sha = asset(backend)[2]
    if digest(archive) != expected_sha:
        raise ValueError("내장 AI 런타임 압축 파일의 SHA-256이 일치하지 않습니다.")
    archives = [archive]
    if backend == "cuda":
        if cudart_archive is None or digest(cudart_archive) != CUDART_SHA256:
            raise ValueError("CUDA 의존 DLL 압축 파일의 SHA-256이 일치하지 않습니다.")
        archives.append(cudart_archive)
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="llama-stage-", dir=directory.parent) as temp:
        staged = Path(temp) / "bundle"
        staged.mkdir()
        names: set[str] = set()
        for source_archive in archives:
            with zipfile.ZipFile(source_archive) as package:
                for member in package.infolist():
                    name = member.filename
                    if not safe_name(name) or name.casefold() in names:
                        raise ValueError("내장 AI 압축 파일에 잘못된 경로가 있습니다.")
                    names.add(name.casefold())
                    if name == "llama-server.exe" or name.endswith(".dll") or name.startswith("LICENSE"):
                        with package.open(member) as source, (staged / name).open("wb") as output:
                            shutil.copyfileobj(source, output, length=1024 * 1024)
        (staged / LICENSE.name).write_bytes(LICENSE.read_bytes())
        if backend == "cuda":
            (staged / CUDA_NOTICE.name).write_bytes(CUDA_NOTICE.read_bytes())
        manifest = {
            "version": VERSION,
            "source": asset(backend)[1],
            "archive_sha256": expected_sha,
            "files": {path.name: digest(path) for path in sorted(staged.iterdir())},
        }
        if backend == "cuda":
            manifest["cudart_sha256"] = CUDART_SHA256
        (staged / "runtime-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        validate_bundle(staged, backend=backend)
        if directory.exists():
            validate_bundle(directory, backend=backend)
            if (directory / "runtime-manifest.json").read_bytes() != (staged / "runtime-manifest.json").read_bytes():
                raise ValueError("기존 내장 AI 런타임이 고정 배포본과 다릅니다.")
        else:
            os.replace(staged, directory)
    return directory


def smoke_check(directory: Path) -> None:
    """Check DLL loading without a model or libraries from the build machine."""
    env = {key: value for key, value in os.environ.items() if not key.upper().endswith("_API_KEY")}
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    env["PATH"] = os.pathsep.join((str(windows / "System32"), str(windows)))
    with tempfile.TemporaryDirectory(prefix="llama-smoke-") as temp:
        result = subprocess.run(
            [str((directory / "llama-server.exe").resolve()), "--version"],
            cwd=temp, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if VERSION.removeprefix("b") not in result.stdout + result.stderr:
        raise ValueError("내장 AI 실행 파일의 실제 버전이 일치하지 않습니다.")
    print(f"내장 AI 런타임 실행 확인: {VERSION}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "build" / "runtime-download")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--with-cuda", action="store_true", help="선택 설치용 CUDA 런타임도 준비합니다.")
    args = parser.parse_args()
    backends = BACKENDS if args.with_cuda else ("cpu", "vulkan")
    if args.verify is not None:
        directories = {backend: args.verify / backend for backend in backends}
        for backend, directory in directories.items():
            validate_bundle(directory, backend=backend)
    else:
        archives = {}
        for backend in (*backends, *(("cudart",) if args.with_cuda else ())):
            archives[backend] = args.cache_dir / asset(backend)[0]
            if not archives[backend].is_file():
                download_archive(archives[backend], backend=backend)
        directories = {backend: directory for backend, directory in zip(BACKENDS, (BUNDLE_DIR, VULKAN_DIR, CUDA_DIR)) if backend in backends}
        for backend, directory in directories.items():
            prepare_bundle(archives[backend], directory, backend=backend, cudart_archive=archives.get("cudart"))
    for directory in directories.values():
        if args.smoke:
            smoke_check(directory)
        print(f"내장 AI 런타임 검증 완료: {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
