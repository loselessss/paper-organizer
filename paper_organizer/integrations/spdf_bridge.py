"""Lazy integration boundary for the tracked sPDF submodule."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpdfUnavailable(RuntimeError):
    pass


_windows: list[Any] = []


@dataclass(frozen=True, slots=True)
class SpdfSelection:
    text: str
    pdf_page: int
    bounding_boxes: tuple[tuple[float, float, float, float], ...]
    document_id: str
    document_path: Path
    requires_ocr: bool = False


def _normalized_selection(value: Any) -> SpdfSelection | None:
    if value is None:
        return None
    boxes = tuple(
        tuple(float(coordinate) for coordinate in box)
        for box in value.bounding_boxes
    )
    if any(len(box) != 4 for box in boxes):
        raise SpdfUnavailable("sPDF 선택 영역 좌표 형식이 올바르지 않습니다.")
    return SpdfSelection(
        text=str(value.text),
        pdf_page=int(value.pdf_page),
        bounding_boxes=boxes,
        document_id=str(value.document_id),
        document_path=Path(value.document_path).resolve(),
        requires_ocr=bool(value.requires_ocr),
    )


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


def open_pdf(
    path: str | Path,
    parent: Any = None,
    *,
    document_id: str = "",
    selection_callback: Any = None,
) -> Any:
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
            tab = window.open_in_tab(str(pdf_path))
            _attach_selection(tab, document_id, selection_callback)
            return window

    window = AppWindow()
    window.destroyed.connect(lambda: _forget_window(window))
    tab = window.open_in_tab(str(pdf_path))
    _attach_selection(tab, document_id, selection_callback)
    window.show()
    _windows.append(window)
    return window


def _attach_selection(tab: Any, document_id: str, callback: Any) -> None:
    if hasattr(tab, "set_selection_document_id"):
        tab.set_selection_document_id(document_id)
    if callback is None or not hasattr(tab, "selection_changed"):
        return
    wrapper = lambda value: callback(_normalized_selection(value))
    tab.selection_changed.connect(wrapper)
    callbacks = getattr(tab, "_paper_organizer_selection_callbacks", [])
    callbacks.append(wrapper)
    tab._paper_organizer_selection_callbacks = callbacks


def _forget_window(window: Any) -> None:
    try:
        _windows.remove(window)
    except ValueError:
        pass
