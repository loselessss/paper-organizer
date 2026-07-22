"""Optional PyQt application entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from PyQt5.QtWidgets import QApplication

    from paper_organizer.application.ai_settings import AiSettingsController
    from paper_organizer.application.summary_service import ImmediateSummaryController
    from paper_organizer.infra.secrets import default_secret_store
    from paper_organizer.ui.main_window import PaperOrganizerWindow

    app = QApplication.instance() or QApplication(sys.argv)
    secret_store = default_secret_store()
    window = PaperOrganizerWindow(
        AiSettingsController(secret_store),
        ImmediateSummaryController(secret_store),
    )
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
