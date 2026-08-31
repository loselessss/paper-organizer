"""Manage the app-bundled local LLM runtime."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from threading import RLock
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from paper_organizer.infra.settings import AppSettings, default_settings_path


DEFAULT_PORT = 11435
DEFAULT_ENDPOINT = f"http://127.0.0.1:{DEFAULT_PORT}/v1/chat/completions"
_MANAGED_PROCESS: subprocess.Popen | None = None
_MANAGED_CONFIG: tuple | None = None
_MANAGED_BACKEND = ""
_PENDING_COMMANDS: list[tuple[str, list[str]]] = []
_RUNTIME_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class EmbeddedLlmRuntimeState:
    executable: Path | None
    model_path: Path | None
    endpoint: str
    available: bool
    error: str = ""


def default_model_dir() -> Path:
    return default_settings_path().parent / "models"


def model_file_name_for_id(model: str) -> str:
    """Return the app-owned GGUF filename for a catalog model id."""

    safe = (
        model.strip()
        .replace("\\", "_")
        .replace("/", "_")
        .replace(":", "_")
        .replace(" ", "_")
    )
    return f"{safe}.gguf" if safe else ""


def model_path_for_id(model: str, model_dir: Path | None = None) -> Path:
    """Return the expected app-owned GGUF path for a catalog model id."""

    return (model_dir or default_model_dir()) / model_file_name_for_id(model)


def model_path_for(settings: AppSettings) -> Path:
    """Return the expected local GGUF path for the selected model id."""

    return model_path_for_id(settings.selected_model.strip())


def bundled_server(backend: str) -> Path | None:
    """Find only the pinned, app-owned executable for one backend."""
    if backend not in {"cuda", "vulkan", "cpu"}:
        raise ValueError("지원하지 않는 내장 AI 실행 방식입니다.")
    candidates: list[Path] = []
    if backend == "cuda":
        from paper_organizer.infra.llama_bundle import VERSION

        candidates.append(default_settings_path().parent / "runtimes" / f"{VERSION}-cuda" / "llama-server.exe")
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                root / "llm" / backend / "llama-server.exe",
                root / "_internal" / "llm" / backend / "llama-server.exe",
            ]
        )
    project_root = Path(__file__).resolve().parents[2]
    if not getattr(sys, "frozen", False):
        folder = "b10715" if backend == "cpu" else f"b10715-{backend}"
        candidates.append(project_root / "build" / "llama-runtime" / folder / "llama-server.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def bundled_llama_server() -> Path | None:
    return next((path for backend in ("cuda", "vulkan", "cpu") if (path := bundled_server(backend))), None)


def _child_environment() -> dict[str, str]:
    # Do not let external llama.cpp defaults change model/device/listen settings.
    return {key: value for key, value in os.environ.items()
            if not key.upper().endswith("_API_KEY") and not key.upper().startswith("LLAMA_ARG_")}


def _devices(executable: Path, backend: str, allow_integrated: bool) -> list[str]:
    try:
        result = subprocess.run(
            [str(executable), "--list-devices"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=True,
            env=_child_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    devices = re.findall(r"^\s*((?:CUDA|Vulkan)\d+): (.+?) \(\d+ MiB", result.stdout, re.MULTILINE)
    if backend == "vulkan":
        from paper_organizer.infra.vulkan_devices import device_types

        types = device_types()
        devices = [(device, name) for device, name in devices if allow_integrated or types.get(name) == 2]
        devices.sort(key=lambda item: types.get(item[1]) != 2)
    return [device for device, _name in devices if device.lower().startswith(backend)]


def _runtime_commands(settings: AppSettings) -> list[tuple[str, list[str]]]:
    result = []
    for backend in ("cuda", "vulkan", "cpu"):
        executable = bundled_server(backend)
        if executable is None:
            continue
        devices = [] if backend == "cpu" else _devices(executable, backend, settings.ollama_force_igpu)
        if backend != "cpu" and not devices:
            continue
        command = [str(executable), "--model", str(model_path_for(settings)),
                   "--alias", settings.selected_model, "--host", "127.0.0.1",
                   "--port", str(DEFAULT_PORT), "--ctx-size", "8192",
                   "--device", devices[0] if devices else "none",
                   "--gpu-layers", "auto" if devices else "0"]
        result.append((backend, command))
    return result


def runtime_backend() -> str:
    """Return the active backend, including a fallback chosen at startup."""
    with _RUNTIME_LOCK:
        return _MANAGED_BACKEND if _MANAGED_PROCESS is not None and _MANAGED_PROCESS.poll() is None else ""


def _launch_next() -> bool:
    global _MANAGED_PROCESS, _MANAGED_BACKEND
    while _PENDING_COMMANDS:
        backend, command = _PENDING_COMMANDS.pop(0)
        try:
            _MANAGED_PROCESS = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=_child_environment(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            _MANAGED_BACKEND = backend
            return True
        except OSError:
            continue
    _MANAGED_PROCESS = None
    _MANAGED_BACKEND = ""
    return False


def inspect_runtime(settings: AppSettings) -> EmbeddedLlmRuntimeState:
    executable = bundled_llama_server()
    model_path = model_path_for(settings)
    if executable is None:
        return EmbeddedLlmRuntimeState(
            None,
            model_path,
            DEFAULT_ENDPOINT,
            False,
            "내장 AI 실행 파일(llama-server.exe)을 찾을 수 없습니다.",
        )
    if not model_path.is_file():
        return EmbeddedLlmRuntimeState(
            executable,
            model_path,
            DEFAULT_ENDPOINT,
            False,
            "선택한 GGUF 모델 파일을 찾을 수 없습니다.",
        )
    return EmbeddedLlmRuntimeState(executable, model_path, DEFAULT_ENDPOINT, True)


def start_runtime(settings: AppSettings) -> bool:
    """Start the bundled llama.cpp server when it is not already managed."""

    global _MANAGED_CONFIG, _PENDING_COMMANDS
    with _RUNTIME_LOCK:
        if settings.bibliography_only:
            stop_runtime()
            return False
        config = (str(model_path_for(settings)), settings.ollama_force_igpu)
        if _MANAGED_CONFIG == config and _MANAGED_PROCESS is not None and _MANAGED_PROCESS.poll() is None:
            return True
        stop_runtime()
        if not inspect_runtime(settings).available:
            return False
        _MANAGED_CONFIG = config
        _PENDING_COMMANDS = _runtime_commands(settings)
        return _launch_next()


def _healthy() -> bool:
    try:
        with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{DEFAULT_PORT}/health", timeout=1) as response:
            return response.status == 200
    except (URLError, OSError):
        return False


def wait_until_ready(timeout: float = 120) -> bool:
    """Wait in an inference worker and fall back after failed GPU startup."""
    deadline = time.monotonic() + timeout
    attempt_started = time.monotonic()
    with _RUNTIME_LOCK:
        expected_config = _MANAGED_CONFIG
    while time.monotonic() < deadline:
        with _RUNTIME_LOCK:
            if _MANAGED_CONFIG != expected_config or _MANAGED_PROCESS is None:
                return False
            process = _MANAGED_PROCESS
            expired = _MANAGED_BACKEND != "cpu" and time.monotonic() - attempt_started >= min(30, timeout / 3)
            if process.poll() is not None or expired:
                _stop_process(process)
                if not _launch_next():
                    return False
                attempt_started = time.monotonic()
                continue
        if _healthy():
            with _RUNTIME_LOCK:
                return _MANAGED_PROCESS is process and process.poll() is None
        time.sleep(0.2)
    with _RUNTIME_LOCK:
        if _MANAGED_CONFIG == expected_config:
            stop_runtime()
    return False


def _stop_process(process) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def stop_runtime() -> None:
    global _MANAGED_PROCESS, _MANAGED_CONFIG, _MANAGED_BACKEND, _PENDING_COMMANDS
    with _RUNTIME_LOCK:
        _stop_process(_MANAGED_PROCESS)
        _MANAGED_PROCESS = None
        _MANAGED_CONFIG = None
        _MANAGED_BACKEND = ""
        _PENDING_COMMANDS = []
