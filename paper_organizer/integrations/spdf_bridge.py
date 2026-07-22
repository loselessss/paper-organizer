"""Lazy integration boundary for the tracked sPDF submodule."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any


class SpdfUnavailable(RuntimeError):
    pass


_windows: list[Any] = []


def spdf_root() -> Path:
    return Path(__file__).resolve().parents[2] / "vendor" / "spdf"


def spdf_available() -> bool:
    root = spdf_root()
    return (root / "pdfeditor" / "app.py").is_file() and (
        root / "pdfeditor" / "meta.py"
    ).is_file()


def spdf_version() -> str | None:
    meta = spdf_root() / "pdfeditor" / "meta.py"
    if not meta.is_file():
        return None
    try:
        tree = ast.parse(meta.read_text(encoding="utf-8"), filename=str(meta))
    except (OSError, SyntaxError, UnicodeError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "APP_VERSION"
            for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    return None


def _ensure_import_path() -> None:
    root = spdf_root()
    if not spdf_available():
        raise SpdfUnavailable(
            "sPDF submodule이 없습니다. git submodule update --init --recursive를 실행하세요."
        )
    value = str(root)
    if value not in sys.path:
        sys.path.insert(0, value)


def open_pdf(path: str | Path, parent: Any = None) -> Any:
    """Open a PDF in an sPDF top-level window using the current QApplication."""
    del parent  # Reserved for a future embedded SpdfWorkspace implementation.
    pdf_path = Path(path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    _ensure_import_path()
    try:
        from PyQt5.QtWidgets import QApplication
        from pdfeditor.app import AppWindow
    except ImportError as exc:
        raise SpdfUnavailable("sPDF를 열려면 PyQt5 런타임이 필요합니다.") from exc
    if QApplication.instance() is None:
        raise SpdfUnavailable("Paper Organizer QApplication이 시작되지 않았습니다.")

    for window in tuple(_windows):
        try:
            existing = window._find_open_tab(str(pdf_path))
        except RuntimeError:
            _windows.remove(window)
            continue
        if existing is not None:
            window.show()
            window.raise_()
            window.activateWindow()
            window.open_in_tab(str(pdf_path))
            return window

    window = AppWindow()
    window.destroyed.connect(lambda: _forget_window(window))
    window.open_in_tab(str(pdf_path))
    window.show()
    _windows.append(window)
    return window


def _forget_window(window: Any) -> None:
    try:
        _windows.remove(window)
    except ValueError:
        pass
