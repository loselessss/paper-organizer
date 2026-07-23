"""First-run, Windows login startup, and window lifecycle preferences."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol, Sequence

from paper_organizer.infra.settings import (
    AppSettings,
    default_settings_path,
    load_settings,
    save_settings,
)


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "PaperOrganizer"


class LifecycleSettingsError(RuntimeError):
    pass


class LoginStartupBackend(Protocol):
    def set_enabled(self, enabled: bool) -> None: ...


def default_startup_command() -> tuple[str, ...]:
    """Build a per-user login command for source, venv, and frozen builds."""

    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return str(executable), "--background"
    if executable.name.casefold() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            executable = pythonw
    return str(executable), "-m", "paper_organizer.gui", "--background"


class WindowsLoginStartup:
    """Manage the current user's HKCU Run entry without administrator rights."""

    def __init__(self, command: Sequence[str] | None = None) -> None:
        self.command = tuple(command or default_startup_command())

    def set_enabled(self, enabled: bool) -> None:
        if os.name != "nt":
            if enabled:
                raise LifecycleSettingsError(
                    "Windows 로그인 자동 시작은 Windows에서만 지원됩니다."
                )
            return
        try:
            import winreg

            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                RUN_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                if enabled:
                    winreg.SetValueEx(
                        key,
                        RUN_VALUE_NAME,
                        0,
                        winreg.REG_SZ,
                        subprocess.list2cmdline(list(self.command)),
                    )
                else:
                    try:
                        winreg.DeleteValue(key, RUN_VALUE_NAME)
                    except FileNotFoundError:
                        pass
        except (OSError, ImportError) as exc:
            raise LifecycleSettingsError(
                f"Windows 로그인 자동 시작 설정을 변경할 수 없습니다: {exc}"
            ) from None


class LifecycleSettingsController:
    def __init__(
        self,
        settings_path: Path | None = None,
        login_startup: LoginStartupBackend | None = None,
    ) -> None:
        self._settings_path = settings_path or default_settings_path()
        self._login_startup = login_startup or WindowsLoginStartup()

    def settings(self) -> AppSettings:
        return load_settings(self._settings_path)

    def first_run_required(self) -> bool:
        return not self.settings().first_run_completed

    def save_preferences(
        self,
        *,
        start_with_windows: bool,
        close_behavior: str,
    ) -> AppSettings:
        if close_behavior not in {"background", "quit"}:
            raise LifecycleSettingsError("창 닫기 동작을 먼저 선택하세요.")
        current = self.settings()
        previous_startup = current.start_with_windows
        try:
            self._login_startup.set_enabled(bool(start_with_windows))
            current.start_with_windows = bool(start_with_windows)
            current.close_behavior = close_behavior
            current.first_run_completed = True
            save_settings(current, self._settings_path)
        except Exception as exc:
            try:
                self._login_startup.set_enabled(previous_startup)
            except Exception:
                pass
            if isinstance(exc, LifecycleSettingsError):
                raise
            raise LifecycleSettingsError(f"시작 및 종료 설정을 저장할 수 없습니다: {exc}") from None
        return current
