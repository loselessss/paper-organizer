"""Find PDF files only after their download appears complete and stable."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PDF_SIGNATURE = b"%PDF-"
PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp", ".download")


@dataclass(frozen=True, slots=True)
class FileObservation:
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class StablePdf:
    path: Path
    observation: FileObservation


def has_pdf_signature(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(len(PDF_SIGNATURE)) == PDF_SIGNATURE
    except OSError:
        return False


def _has_related_partial_file(path: Path) -> bool:
    parent = path.parent
    name = path.name
    stem = path.stem
    for suffix in PARTIAL_SUFFIXES:
        if (parent / f"{name}{suffix}").exists() or (parent / f"{stem}{suffix}").exists():
            return True
    return False


def iter_pdf_candidates(root: Path, recursive: bool = False) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    pattern = "**/*" if recursive else "*"
    return (
        path
        for path in root.glob(pattern)
        if path.is_file() and path.suffix.casefold() == ".pdf"
    )


class DiscoveryTracker:
    """Stateful two-scan detector that avoids files still being downloaded."""

    def __init__(self) -> None:
        self._previous: dict[Path, FileObservation] = {}

    def scan(
        self,
        root: Path,
        *,
        recursive: bool = False,
        minimum_age_seconds: int = 30,
        now: float | None = None,
    ) -> list[StablePdf]:
        current_time = time.time() if now is None else now
        current: dict[Path, FileObservation] = {}
        stable: list[StablePdf] = []

        for path in iter_pdf_candidates(root, recursive):
            try:
                stat = path.stat()
            except OSError:
                continue
            observation = FileObservation(stat.st_size, stat.st_mtime_ns)
            current[path] = observation
            age_seconds = current_time - (stat.st_mtime_ns / 1_000_000_000)
            if age_seconds < minimum_age_seconds:
                continue
            if _has_related_partial_file(path):
                continue
            if self._previous.get(path) != observation:
                continue
            if not has_pdf_signature(path):
                continue
            stable.append(StablePdf(path=path, observation=observation))

        self._previous = current
        return sorted(stable, key=lambda item: (item.observation.modified_ns, str(item.path)))

    def forget(self, path: Path) -> None:
        self._previous.pop(path, None)
