"""Prepare and verify the pinned Windows x64 llama.cpp runtime bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.request import urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "b10715"
ARCHIVE_NAME = f"llama-{VERSION}-bin-win-cpu-x64.zip"
URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{VERSION}/{ARCHIVE_NAME}"
SHA256 = "b01268e9d933477f5c12a0b89d1ecde69e4b914ec4a1e0ce52ed1cfcf563d19e"
BUNDLE_DIR = ROOT / "build" / "llama-runtime" / VERSION
LICENSE = ROOT / "scripts" / "licenses" / "llama.cpp-LICENSE.txt"
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


def download_archive(path: Path) -> None:
    """Download atomically and reject any bytes other than the pinned archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="llama-download-", dir=path.parent) as temp:
        partial = Path(temp) / ARCHIVE_NAME
        with urlopen(URL, timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if digest(partial) != SHA256:
            raise ValueError("내장 AI 런타임 다운로드의 SHA-256이 일치하지 않습니다.")
        os.replace(partial, path)


def validate_bundle(directory: Path) -> None:
    """Fail closed on missing, stale, altered, or incomplete runtime files."""
    manifest = json.loads((directory / "runtime-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != VERSION or manifest.get("archive_sha256") != SHA256:
        raise ValueError("내장 AI 런타임 버전 또는 원본 해시가 일치하지 않습니다.")
    files = manifest.get("files", {})
    if not isinstance(files, dict) or not REQUIRED <= files.keys():
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


def prepare_bundle(archive: Path, directory: Path = BUNDLE_DIR) -> Path:
    """Stage the server, every companion DLL and licenses before publishing."""
    if digest(archive) != SHA256:
        raise ValueError("내장 AI 런타임 압축 파일의 SHA-256이 일치하지 않습니다.")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="llama-stage-", dir=directory.parent) as temp:
        staged = Path(temp) / "bundle"
        staged.mkdir()
        with zipfile.ZipFile(archive) as package:
            names: set[str] = set()
            for member in package.infolist():
                name = member.filename
                if not safe_name(name) or name in names:
                    raise ValueError("내장 AI 압축 파일에 잘못된 경로가 있습니다.")
                names.add(name)
                if name == "llama-server.exe" or name.endswith(".dll") or name.startswith("LICENSE"):
                    (staged / name).write_bytes(package.read(member))
        (staged / LICENSE.name).write_bytes(LICENSE.read_bytes())
        manifest = {
            "version": VERSION,
            "source": URL,
            "archive_sha256": SHA256,
            "files": {path.name: digest(path) for path in sorted(staged.iterdir())},
        }
        (staged / "runtime-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        validate_bundle(staged)
        if directory.exists():
            validate_bundle(directory)
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
    parser.add_argument("--archive", type=Path, default=ROOT / "build" / "runtime-download" / ARCHIVE_NAME)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.verify is not None:
        directory = args.verify
        validate_bundle(directory)
    else:
        if not args.archive.is_file():
            download_archive(args.archive)
        directory = prepare_bundle(args.archive)
    if args.smoke:
        smoke_check(directory)
    print(f"내장 AI 런타임 검증 완료: {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
