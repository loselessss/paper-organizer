# Ollama 런타임 설치 여부를 확인하고 winget으로 설치·기동까지 처리하는 모듈
"""Detect, install and start the local Ollama runtime on Windows.

설치는 사용자가 명시적으로 요청했을 때만 실행한다. winget이 없거나 실패하면
공식 다운로드 페이지를 안내하고, 앱이 임의로 다른 곳에서 파일을 받지 않는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from paper_organizer.infra.ollama_runtime import OllamaRuntimeInspector
from paper_organizer.infra.secrets import sanitized_child_environment

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
WINGET_PACKAGE_ID = "Ollama.Ollama"
_INSTALL_TIMEOUT_SECONDS = 180
_START_TIMEOUT_SECONDS = 60
_OLLAMA_LOOPBACK_HOST = "127.0.0.1:11434"

CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess]
RuntimeLauncher = Callable[[str, dict[str, str]], None]
_managed_process: subprocess.Popen | None = None


def _is_accessible_file(path: Path) -> bool:
    """Return whether path is a file without failing on protected aliases."""

    try:
        return path.is_file()
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class OllamaRuntimeState:
    installed: bool
    running: bool
    version: str
    executable: str = ""
    can_install_with_winget: bool = False
    message: str = ""


@dataclass(frozen=True, slots=True)
class OllamaSetupResult:
    ok: bool
    state: OllamaRuntimeState
    message: str
    needs_manual_download: bool = False


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=sanitized_child_environment(),
        creationflags=creation_flags,
    )


def find_ollama_executable() -> str:
    """Return the ollama executable path, including the default install dir."""

    found = shutil.which("ollama")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
    ]
    for candidate in candidates:
        if candidate.parent.name and _is_accessible_file(candidate):
            return str(candidate)
    return ""


def find_winget_executable() -> str:
    """Return the winget path, including the Store alias folder.

    winget이 설치되어 있어도 앱이 상속한 PATH에 `WindowsApps`가 빠져 있으면
    `shutil.which`가 찾지 못한다. 기본 설치 경로까지 확인해 그때도 자동 설치를
    쓸 수 있게 한다.
    """

    found = shutil.which("winget")
    if found:
        return found
    candidate = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "WindowsApps"
        / "winget.exe"
    )
    if candidate.parent.parent.name and _is_accessible_file(candidate):
        return str(candidate)
    return ""


def winget_available() -> bool:
    return bool(find_winget_executable())


def inspect_runtime(
    inspector: OllamaRuntimeInspector | None = None,
) -> OllamaRuntimeState:
    """Report whether Ollama is installed and whether its server answers."""

    status = (inspector or OllamaRuntimeInspector()).inspect()
    executable = find_ollama_executable()
    installed = bool(executable) or status.reachable
    if status.reachable:
        message = f"Ollama {status.version}이(가) 실행 중입니다."
    elif installed:
        message = "Ollama가 설치되어 있지만 실행 중이 아닙니다."
    else:
        message = "Ollama가 설치되어 있지 않습니다."
    return OllamaRuntimeState(
        installed=installed,
        running=status.reachable,
        version=status.version,
        executable=executable,
        can_install_with_winget=winget_available(),
        message=message,
    )


def start_runtime(
    *,
    inspector: OllamaRuntimeInspector | None = None,
    timeout_seconds: int = _START_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Start `ollama serve` in the background and wait until it answers."""

    probe = inspector or OllamaRuntimeInspector()
    if probe.inspect().reachable:
        return True
    executable = find_ollama_executable()
    if not executable:
        return False
    global _managed_process
    try:
        environment = sanitized_child_environment()
        # The application always inspects the loopback endpoint. Do not inherit a
        # user-level OLLAMA_HOST that would make the managed server listen elsewhere.
        environment["OLLAMA_HOST"] = _OLLAMA_LOOPBACK_HOST
        _managed_process = subprocess.Popen(
            [executable, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return False
    deadline = timeout_seconds
    while deadline > 0:
        if probe.inspect().reachable:
            return True
        if _managed_process.poll() is not None:
            _managed_process = None
            return False
        sleep(1.0)
        deadline -= 1
    stop_managed_runtime()
    return False


def stop_managed_runtime() -> bool:
    """Stop only the Ollama server process started by this application."""

    global _managed_process
    process = _managed_process
    _managed_process = None
    if process is None or process.poll() is not None:
        return False
    try:
        process.terminate()
        process.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass
    return True


def _launch_desktop_runtime(executable: str, environment: dict[str, str]) -> None:
    """Launch the Windows tray app, which owns its local server child."""

    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    subprocess.Popen(
        [executable],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        creationflags=flags,
    )


def restart_runtime(
    *,
    inspector: OllamaRuntimeInspector | None = None,
    run_command: CommandRunner = _run,
    launcher: RuntimeLauncher | None = None,
    sleep: Callable[[float], None] = time.sleep,
    timeout_seconds: int = _START_TIMEOUT_SECONDS,
) -> bool:
    """Restart the shared Windows Ollama tray/server after a GPU setting change."""

    executable = find_ollama_executable()
    if not executable:
        return False
    probe = inspector or OllamaRuntimeInspector()
    stop_managed_runtime()
    if os.name == "nt":
        try:
            run_command(("taskkill", "/IM", "ollama.exe", "/T", "/F"), 15)
        except (OSError, subprocess.SubprocessError):
            return False
    else:
        return start_runtime(
            inspector=probe,
            timeout_seconds=timeout_seconds,
            sleep=sleep,
        )
    for _ in range(20):
        if not probe.inspect().reachable:
            break
        sleep(0.25)
    else:
        return False
    environment = sanitized_child_environment()
    environment["OLLAMA_HOST"] = _OLLAMA_LOOPBACK_HOST
    try:
        (launcher or _launch_desktop_runtime)(executable, environment)
    except OSError:
        return False
    for _ in range(max(1, timeout_seconds * 2)):
        if probe.inspect().reachable:
            return True
        sleep(0.5)
    return False


def ensure_runtime(
    *,
    allow_install: bool,
    inspector: OllamaRuntimeInspector | None = None,
    run_command: CommandRunner = _run,
    start: Callable[..., bool] | None = None,
) -> OllamaSetupResult:
    """Make Ollama usable, installing it only when the user allowed it."""

    probe = inspector or OllamaRuntimeInspector()
    starter = start or (lambda: start_runtime(inspector=probe))
    state = inspect_runtime(probe)
    if state.running:
        return OllamaSetupResult(True, state, state.message)
    if state.installed:
        if starter():
            state = inspect_runtime(probe)
            return OllamaSetupResult(True, state, "Ollama를 실행했습니다.")
        return OllamaSetupResult(
            False,
            state,
            "Ollama가 설치되어 있지만 실행하지 못했습니다. "
            "Ollama를 직접 실행한 뒤 다시 시도하세요.",
        )
    if not allow_install:
        return OllamaSetupResult(
            False, state, "Ollama가 설치되어 있지 않습니다.", needs_manual_download=True
        )
    winget = find_winget_executable()
    if not winget:
        return OllamaSetupResult(
            False,
            state,
            "이 PC에서는 winget을 쓸 수 없습니다. "
            f"{OLLAMA_DOWNLOAD_URL} 에서 설치 프로그램을 내려받아 설치하세요.",
            needs_manual_download=True,
        )
    try:
        completed = run_command(
            [
                winget,
                "install",
                "--id",
                WINGET_PACKAGE_ID,
                "--exact",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            _INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return OllamaSetupResult(
            False,
            inspect_runtime(probe),
            "winget 자동 설치가 3분 동안 완료되지 않아 중단했습니다. "
            "Windows의 Delivery Optimization 상태를 확인하거나 Ollama 공식 "
            "설치 프로그램을 사용하세요.",
            needs_manual_download=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return OllamaSetupResult(
            False,
            state,
            f"winget 설치를 실행하지 못했습니다: {exc}. "
            f"{OLLAMA_DOWNLOAD_URL} 에서 직접 설치할 수 있습니다.",
            needs_manual_download=True,
        )
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout or "").split())[:300]
        return OllamaSetupResult(
            False,
            inspect_runtime(probe),
            f"winget 설치가 실패했습니다({completed.returncode}). {detail} "
            f"{OLLAMA_DOWNLOAD_URL} 에서 직접 설치할 수 있습니다.",
            needs_manual_download=True,
        )
    if starter():
        return OllamaSetupResult(
            True, inspect_runtime(probe), "Ollama를 설치하고 실행했습니다."
        )
    return OllamaSetupResult(
        False,
        inspect_runtime(probe),
        "Ollama를 설치했지만 자동으로 실행하지 못했습니다. "
        "Windows 시작 메뉴에서 Ollama를 실행한 뒤 다시 시도하세요.",
    )
