"""Keep the hosted sPDF caption independent of the organizer theme."""

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QProxyStyle, QStyle, QStyleFactory, QWidget


class _TabStyle(QProxyStyle):
    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_IndicatorTabClose:
            from pdfeditor.icons import fluent_icon
            rect = option.rect
            size = min(12, rect.width(), rect.height())
            fluent_icon("close", size=size).paint(
                painter, rect.center().x() - size // 2,
                rect.center().y() - size // 2, size, size)
            return
        super().drawPrimitive(element, option, painter, widget)


class _NativeTabStyle(QObject):
    def __init__(self, bar):
        super().__init__(bar)
        names = {name.lower(): name for name in QStyleFactory.keys()}
        name = names.get("windowsvista") or names.get("windows")
        self.style = _TabStyle(QStyleFactory.create(name)) if name else None
        if self.style is not None:
            self.style.setParent(self)
            bar.setStyle(self.style)
            for child in bar.findChildren(QWidget):
                child.setStyle(self.style)
            bar.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ChildPolished and self.style is not None:
            child = event.child()
            if isinstance(child, QWidget):
                child.setStyle(self.style)
        return False


def apply_spdf_caption_style(window):
    """Restore upstream caption styling without changing QApplication or sPDF."""
    from pdfeditor.theme import FLUENT_STYLESHEET

    chrome = getattr(window, "_window_chrome", None)
    if not isinstance(chrome, QWidget) or hasattr(chrome, "_organizer_tab_style"):
        return
    chrome._organizer_tab_style = _NativeTabStyle(chrome.bar)
    chrome.setStyleSheet(
        "QWidget { background: transparent; }\n"
        + FLUENT_STYLESHEET + "\n" + chrome.styleSheet()
    )
    chrome.drag_space.setStyleSheet("background: #e9edf2;")
    chrome.brand.setStyleSheet("background: transparent;")
