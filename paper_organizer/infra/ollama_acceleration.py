"""Configure optional Ollama acceleration without starting an AI model."""

from __future__ import annotations

import os
from collections.abc import Callable, MutableMapping


OLLAMA_IGPU_ENVIRONMENT = "OLLAMA_IGPU_ENABLE"

UserValueWriter = Callable[[str, str | None], None]
EnvironmentBroadcaster = Callable[[], None]


def configure_ollama_igpu(
    enabled: bool,
    *,
    user_value_writer: UserValueWriter | None = None,
    broadcaster: EnvironmentBroadcaster | None = None,
    process_environment: MutableMapping[str, str] | None = None,
) -> None:
    """Persist Ollama's iGPU opt-in and update this process for managed starts."""

    writer = user_value_writer or _write_windows_user_environment
    value = "1" if enabled else None
    writer(OLLAMA_IGPU_ENVIRONMENT, value)
    environment = os.environ if process_environment is None else process_environment
    if value is None:
        environment.pop(OLLAMA_IGPU_ENVIRONMENT, None)
    else:
        environment[OLLAMA_IGPU_ENVIRONMENT] = value
    notify = broadcaster or _broadcast_windows_environment_change
    try:
        notify()
    except OSError:
        # The registry value is authoritative. A process started by this app also
        # receives the updated os.environ even if the desktop broadcast is blocked.
        pass


def _write_windows_user_environment(name: str, value: str | None) -> None:
    if os.name != "nt":
        raise RuntimeError("내장 GPU 강제 설정은 Windows에서만 지원합니다.")
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
    ) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _broadcast_windows_environment_change() -> None:
    if os.name != "nt":
        return
    import ctypes

    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x001A
    smto_abortifhung = 0x0002
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd_broadcast,
        wm_settingchange,
        0,
        "Environment",
        smto_abortifhung,
        5000,
        ctypes.byref(result),
    )
