"""Manage the app-bundled local LLM runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from paper_organizer.infra.settings import AppSettings, default_settings_path


DEFAULT_PORT = 11435
DEFAULT_ENDPOINT = f"http://127.0.0.1:{DEFAULT_PORT}/v1/chat/completions"
_MANAGED_PROCESS: subprocess.Popen | None = None


@dataclass(frozen=True, slots=True)
class EmbeddedLlmRuntimeState:
    executable: Path | None
    model_path: Path | None
    endpoint: str
    available: bool
    error: str = ""


def default_model_dir() -> Path:
    return default_settings_path().parent / "models"


def model_path_for(settings: AppSettings) -> Path:
    """Return the expected local GGUF path for the selected model id."""

    model = settings.selected_model.strip()
    if not model:
        return default_model_dir() / ""
    safe = (
        model.replace("\\", "_")
        .replace("/", "_")
        .replace(":", "_")
        .replace(" ", "_")
    )
    return default_model_dir() / f"{safe}.gguf"


def bundled_llama_server() -> Path | None:
    """Find the llama.cpp server executable shipped with the app."""

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                root / "llm" / "llama-server.exe",
                root / "_internal" / "llm" / "llama-server.exe",
            ]
        )
    project_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            project_root / "vendor" / "llama.cpp" / "build" / "bin" / "Release" / "llama-server.exe",
            project_root / "vendor" / "llama.cpp" / "build" / "bin" / "llama-server.exe",
            project_root / "tools" / "llama.cpp" / "llama-server.exe",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


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

    global _MANAGED_PROCESS
    if _MANAGED_PROCESS is not None and _MANAGED_PROCESS.poll() is None:
        return True
    state = inspect_runtime(settings)
    if not state.available or state.executable is None or state.model_path is None:
        return False
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().endswith("_API_KEY")
    }
    command = [
        str(state.executable),
        "--model",
        str(state.model_path),
        "--port",
        str(DEFAULT_PORT),
        "--ctx-size",
        "8192",
    ]
    try:
        _MANAGED_PROCESS = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
    except OSError:
        _MANAGED_PROCESS = None
        return False
    return True


def stop_runtime() -> None:
    global _MANAGED_PROCESS
    process = _MANAGED_PROCESS
    _MANAGED_PROCESS = None
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
