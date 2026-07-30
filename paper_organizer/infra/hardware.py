"""Low-dependency local hardware inspection for model recommendations."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


@dataclass(frozen=True, slots=True)
class GpuInfo:
    name: str
    vendor: str
    vram_total_gb: float | None
    vram_available_gb: float | None
    backend: str


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    detected_at: str
    cpu_model: str
    physical_cores: int | None
    logical_cores: int
    memory_total_gb: float
    memory_available_gb: float
    gpus: tuple[GpuInfo, ...]
    model_disk_path: str
    model_disk_free_gb: float

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["gpus"] = [asdict(gpu) for gpu in self.gpus]
        return value


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _run_command(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=flags,
    )


class HardwareInspector:
    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self._run = command_runner or _run_command

    def inspect(self, model_path: Path | None = None) -> HardwareProfile:
        total, available = _memory_bytes()
        target = (model_path or default_ollama_models_path()).expanduser().resolve()
        disk_root = _existing_ancestor(target)
        disk = shutil.disk_usage(disk_root)
        return HardwareProfile(
            detected_at=datetime.now(timezone.utc).isoformat(),
            cpu_model=_cpu_model(),
            physical_cores=_physical_cores(),
            logical_cores=os.cpu_count() or 1,
            memory_total_gb=_gib(total),
            memory_available_gb=_gib(available),
            gpus=self._gpus(),
            model_disk_path=str(target),
            model_disk_free_gb=_gib(disk.free),
        )

    def available_memory_gb(self) -> float:
        """Return current available system memory without the slower GPU scan."""

        _total, available = _memory_bytes()
        return _gib(available)

    def total_memory_gb(self) -> float:
        """Return installed system memory without the slower GPU scan."""

        total, _available = _memory_bytes()
        return _gib(total)

    def _gpus(self) -> tuple[GpuInfo, ...]:
        nvidia = self._nvidia_gpus()
        if nvidia:
            return nvidia
        return self._windows_video_controllers()

    def _nvidia_gpus(self) -> tuple[GpuInfo, ...]:
        command = (
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        )
        try:
            result = self._run(command, 4.0)
        except (OSError, subprocess.SubprocessError):
            return ()
        if result.returncode != 0:
            return ()
        values: list[GpuInfo] = []
        for line in result.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 3:
                continue
            try:
                total_mib = float(parts[1])
                free_mib = float(parts[2])
            except ValueError:
                continue
            values.append(
                GpuInfo(
                    name=parts[0],
                    vendor="NVIDIA",
                    vram_total_gb=round(total_mib / 1024, 2),
                    vram_available_gb=round(free_mib / 1024, 2),
                    backend="CUDA",
                )
            )
        return tuple(values)

    def _windows_video_controllers(self) -> tuple[GpuInfo, ...]:
        if os.name != "nt":
            return ()
        script = (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"
        )
        try:
            result = self._run(("powershell", "-NoProfile", "-Command", script), 6.0)
        except (OSError, subprocess.SubprocessError):
            return ()
        if result.returncode != 0 or not result.stdout.strip():
            return ()
        try:
            decoded = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ()
        rows = decoded if isinstance(decoded, list) else [decoded]
        values: list[GpuInfo] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("Name"):
                continue
            name = str(row["Name"])
            lowered = name.casefold()
            vendor = (
                "AMD"
                if "amd" in lowered or "radeon" in lowered
                else "Intel"
                if "intel" in lowered
                else "Unknown"
            )
            raw_ram = row.get("AdapterRAM")
            vram = _gib(raw_ram) if isinstance(raw_ram, int) and raw_ram > 0 else None
            values.append(
                GpuInfo(
                    name=name,
                    vendor=vendor,
                    vram_total_gb=vram,
                    vram_available_gb=None,
                    backend="DirectML candidate",
                )
            )
        return tuple(values)


def default_ollama_models_path() -> Path:
    configured = os.environ.get("OLLAMA_MODELS")
    if configured:
        return Path(configured)
    return Path.home() / ".ollama" / "models"


def _cpu_model() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except (OSError, ImportError):
            pass
    value = platform.processor().strip()
    if value:
        return value
    return platform.machine() or "Unknown CPU"


def _physical_cores() -> int | None:
    try:
        import psutil

        value = psutil.cpu_count(logical=False)
        return int(value) if value else None
    except (ImportError, OSError, ValueError):
        pass
    if os.name == "nt":
        try:
            class CacheDescriptor(ctypes.Structure):
                _fields_ = [
                    ("level", ctypes.c_byte),
                    ("associativity", ctypes.c_byte),
                    ("line_size", ctypes.c_ushort),
                    ("size", ctypes.c_ulong),
                    ("cache_type", ctypes.c_int),
                ]

            class RelationshipData(ctypes.Union):
                _fields_ = [
                    ("processor_core_flags", ctypes.c_byte),
                    ("numa_node_number", ctypes.c_ulong),
                    ("cache", CacheDescriptor),
                    ("reserved", ctypes.c_ulonglong * 2),
                ]

            class LogicalProcessorInfo(ctypes.Structure):
                _anonymous_ = ("data",)
                _fields_ = [
                    ("processor_mask", ctypes.c_size_t),
                    ("relationship", ctypes.c_int),
                    ("data", RelationshipData),
                ]

            required = ctypes.c_ulong(0)
            kernel = ctypes.windll.kernel32
            kernel.GetLogicalProcessorInformation(None, ctypes.byref(required))
            if required.value:
                buffer = ctypes.create_string_buffer(required.value)
                if kernel.GetLogicalProcessorInformation(
                    buffer, ctypes.byref(required)
                ):
                    item_size = ctypes.sizeof(LogicalProcessorInfo)
                    count = required.value // item_size
                    items = ctypes.cast(
                        buffer, ctypes.POINTER(LogicalProcessorInfo)
                    )
                    cores = sum(
                        1 for index in range(count) if items[index].relationship == 0
                    )
                    if cores:
                        return cores
        except (AttributeError, OSError, ValueError):
            pass
    return None


def _memory_bytes() -> tuple[int, int]:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_physical), int(status.available_physical)
    try:
        import psutil

        memory = psutil.virtual_memory()
        return int(memory.total), int(memory.available)
    except (ImportError, OSError, ValueError):
        return 0, 0


def _existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _gib(value: int | float) -> float:
    return round(float(value) / (1024**3), 2)
