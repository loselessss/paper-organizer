"""Shared presentation rules for application dialogs."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog


def suppress_context_help_button(dialog: QDialog) -> None:
    """Remove Qt's unused title-bar help button from an application dialog."""

    dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
