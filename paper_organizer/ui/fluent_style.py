"""Fluent-inspired application theme helpers."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PyQt5.QtWidgets import QAction, QApplication, QStyleFactory, QToolButton


_THEME_PROPERTY = "paperOrganizerFluentThemeApplied"

_GLYPHS = {
    "add": "\ue710",
    "ai": "\ue8f2",
    "archive": "\ue7b8",
    "back": "\ue72b",
    "cancel": "\ue711",
    "check": "\ue73e",
    "copy": "\ue8c8",
    "delete": "\ue74d",
    "download": "\ue896",
    "edit": "\ue70f",
    "folder": "\ue8b7",
    "help": "\ue897",
    "library": "\ue8f1",
    "link": "\ue71b",
    "menu": "\ue700",
    "open": "\ue8e5",
    "pdf": "\ue8a5",
    "priority": "\ue735",
    "refresh": "\ue72c",
    "restore": "\ue777",
    "save": "\ue74e",
    "search": "\ue721",
    "select": "\ue762",
    "settings": "\ue713",
    "stop": "\ue71a",
    "translate": "\ue8e2",
    "upload": "\ue898",
}


def apply_fluent_theme(app: QApplication | None) -> None:
    """Apply a calm Fluent 2 inspired theme to the Qt application."""

    if app is None or app.property(_THEME_PROPERTY):
        return

    available_styles = {name.lower(): name for name in QStyleFactory.keys()}
    if "fusion" in available_styles:
        app.setStyle(QStyleFactory.create(available_styles["fusion"]))

    font = QFont("Segoe UI", 9)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#f7f7f7"))
    palette.setColor(QPalette.WindowText, QColor("#1f1f1f"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f5f5f5"))
    palette.setColor(QPalette.ToolTipBase, QColor("#ffffff"))
    palette.setColor(QPalette.ToolTipText, QColor("#1f1f1f"))
    palette.setColor(QPalette.Text, QColor("#1f1f1f"))
    palette.setColor(QPalette.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ButtonText, QColor("#1f1f1f"))
    palette.setColor(QPalette.Highlight, QColor("#e4e4e4"))
    palette.setColor(QPalette.HighlightedText, QColor("#1f1f1f"))
    palette.setColor(QPalette.Link, QColor("#555555"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#8a8a8a"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#8a8a8a"))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#8a8a8a"))
    app.setPalette(palette)

    app.setStyleSheet(
        """
        QWidget {
            color: #1f1f1f;
            background: #f7f7f7;
            selection-background-color: #e4e4e4;
            selection-color: #1f1f1f;
        }

        QMainWindow, QDialog {
            background: #f7f7f7;
        }

        QMenuBar {
            background: #f7f7f7;
            border-bottom: 1px solid #e5e5e5;
            padding: 2px 6px;
        }

        QMenuBar::item {
            background: transparent;
            border-radius: 4px;
            padding: 6px 10px;
        }

        QMenuBar::item:selected {
            background: #eeeeee;
        }

        QMenu {
            background: #ffffff;
            border: 1px solid #d9d9d9;
            border-radius: 6px;
            padding: 6px;
        }

        QMenu::item {
            border-radius: 4px;
            padding: 7px 28px 7px 12px;
        }

        QMenu::item:selected {
            background: #eeeeee;
            color: #1f1f1f;
        }

        QToolBar#commandRibbon {
            background: #fafafa;
            border: 0;
            border-bottom: 1px solid #e5e5e5;
            spacing: 4px;
            padding: 6px 8px;
        }

        QToolBar#commandRibbon::separator {
            background: #e0e0e0;
            width: 1px;
            margin: 5px 8px;
        }

        QToolBar#commandRibbon QToolButton {
            background: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            padding: 5px 9px;
            min-width: 64px;
            min-height: 42px;
        }

        QToolBar#commandRibbon QToolButton:hover {
            background: #f0f0f0;
            border-color: #e0e0e0;
        }

        QToolBar#commandRibbon QToolButton:pressed {
            background: #e8e8e8;
            border-color: #d1d1d1;
        }

        QTabWidget::pane {
            border: 1px solid #e5e5e5;
            border-radius: 8px;
            background: #ffffff;
            top: -1px;
        }

        QTabBar::tab {
            background: transparent;
            border: 0;
            border-bottom: 2px solid transparent;
            color: #424242;
            min-width: 104px;
            padding: 10px 18px 8px 18px;
        }

        QTabBar::tab:selected {
            color: #1f1f1f;
            border-bottom-color: #707070;
            font-weight: 600;
        }

        QTabBar::tab:hover:!selected {
            background: #eeeeee;
            border-radius: 6px;
        }

        QGroupBox {
            background: #fbfbfb;
            border: 1px solid #e6e6e6;
            border-radius: 6px;
            margin-top: 14px;
            padding: 12px 10px 10px 10px;
        }

        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            color: #5f5f5f;
            background: #f7f7f7;
            padding: 0 4px;
            left: 8px;
            font-weight: 600;
        }

        QPushButton, QToolButton {
            background: #ffffff;
            border: 1px solid #d1d1d1;
            border-radius: 6px;
            padding: 6px 12px;
            min-height: 24px;
        }

        QPushButton:hover, QToolButton:hover {
            background: #f5f5f5;
            border-color: #c7c7c7;
        }

        QPushButton:pressed, QToolButton:pressed {
            background: #eeeeee;
            border-color: #bdbdbd;
        }

        QPushButton:default {
            background: #f1f1f1;
            border-color: #d1d1d1;
            color: #1f1f1f;
        }

        QPushButton:default:hover {
            background: #e7e7e7;
        }

        QPushButton:disabled, QToolButton:disabled {
            background: #f5f5f5;
            border-color: #e0e0e0;
            color: #8a8a8a;
        }

        QLineEdit, QTextEdit, QTextBrowser, QPlainTextEdit, QComboBox,
        QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget, QTreeWidget {
            background: #ffffff;
            border: 1px solid #d1d1d1;
            border-radius: 6px;
            padding: 5px;
        }

        QTextBrowser, QTextEdit, QPlainTextEdit {
            selection-background-color: #e4e4e4;
        }

        QLineEdit:focus, QTextEdit:focus, QTextBrowser:focus, QPlainTextEdit:focus,
        QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
        QListWidget:focus, QTableWidget:focus, QTreeWidget:focus {
            border: 1px solid #777777;
        }

        QComboBox::drop-down {
            width: 26px;
            border: 0;
        }

        QComboBox::down-arrow {
            image: none;
            border: 0;
            width: 0;
            height: 0;
        }

        QHeaderView::section {
            background: #f3f3f3;
            border: 0;
            border-right: 1px solid #e5e5e5;
            border-bottom: 1px solid #d9d9d9;
            padding: 7px 8px;
            font-weight: 600;
        }

        QTableWidget {
            gridline-color: #ededed;
            alternate-background-color: #fafafa;
        }

        QTableWidget::item, QListWidget::item {
            border-radius: 4px;
            padding: 4px;
        }

        QTableWidget::item:selected, QListWidget::item:selected {
            background: #e4e4e4;
            color: #1f1f1f;
        }

        QSplitter::handle {
            background: #e5e5e5;
        }

        QSplitter::handle:horizontal {
            width: 6px;
        }

        QSplitter::handle:vertical {
            height: 6px;
        }

        QProgressBar {
            background: #eeeeee;
            border: 0;
            border-radius: 4px;
            min-height: 8px;
            text-align: center;
        }

        QProgressBar::chunk {
            background: #6f6f6f;
            border-radius: 4px;
        }

        QScrollBar:vertical {
            background: transparent;
            width: 12px;
            margin: 2px;
        }

        QScrollBar::handle:vertical {
            background: #c7c7c7;
            border-radius: 5px;
            min-height: 28px;
        }

        QScrollBar::handle:vertical:hover {
            background: #a6a6a6;
        }

        QScrollBar:horizontal {
            background: transparent;
            height: 12px;
            margin: 2px;
        }

        QScrollBar::handle:horizontal {
            background: #c7c7c7;
            border-radius: 5px;
            min-width: 28px;
        }

        QScrollBar::handle:horizontal:hover {
            background: #a6a6a6;
        }

        QScrollBar::add-line, QScrollBar::sub-line,
        QScrollBar::add-page, QScrollBar::sub-page {
            background: transparent;
            border: 0;
            width: 0;
            height: 0;
        }

        QStatusBar {
            background: #f7f7f7;
            border-top: 1px solid #e5e5e5;
        }

        QFrame#analysisQueuePopup {
            background: #fafafa;
            border: 1px solid #dddddd;
            border-radius: 8px;
        }

        QToolTip {
            background: #ffffff;
            color: #1f1f1f;
            border: 1px solid #d1d1d1;
            border-radius: 4px;
            padding: 6px;
        }

        QPushButton[fluentRole="primary"] {
            background: #f1f1f1;
            border-color: #d1d1d1;
            color: #1f1f1f;
        }

        QPushButton[fluentRole="primary"]:hover {
            background: #e7e7e7;
            border-color: #c7c7c7;
        }

        QPushButton[fluentRole="destructive"] {
            color: #b42318;
            border-color: #f1b8b2;
            background: #fff7f6;
        }

        QPushButton[fluentRole="destructive"]:hover {
            background: #fde7e9;
            border-color: #e78f87;
        }
        """
    )
    app.setProperty(_THEME_PROPERTY, True)


def fluent_icon(name: str, color: str = "#424242", size: int = 16) -> QIcon:
    """Return a small icon rendered from the Windows Fluent glyph font."""

    glyph = _GLYPHS.get(name, "")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    if not glyph:
        return QIcon(pixmap)
    painter = QPainter(pixmap)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    font = QFont("Segoe MDL2 Assets", max(8, size - 5))
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, glyph)
    painter.end()
    return QIcon(pixmap)


def decorate_button(
    button: QToolButton,
    icon_name: str,
    *,
    role: str = "",
    tooltip: str = "",
) -> None:
    """Attach a Fluent glyph and optional role metadata to a button."""

    color = "#555555" if role == "primary" else "#b42318" if role == "destructive" else "#424242"
    button.setIcon(fluent_icon(icon_name, color=color))
    button.setIconSize(QSize(14, 14))
    if role:
        button.setProperty("fluentRole", role)
        button.style().unpolish(button)
        button.style().polish(button)
    if tooltip and not button.toolTip():
        button.setToolTip(tooltip)


def decorate_action(action: QAction, icon_name: str) -> None:
    """Attach a Fluent glyph to a menu or shortcut action."""

    action.setIcon(fluent_icon(icon_name))
