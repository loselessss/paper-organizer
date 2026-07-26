"""Optional PyQt application entry point."""

from __future__ import annotations

import sys


def main() -> int:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QDialog

    from paper_organizer.application.ai_settings import AiSettingsController
    from paper_organizer.application.background_analysis import BackgroundAnalysisService
    from paper_organizer.application.lifecycle import LifecycleSettingsController
    from paper_organizer.application.library_workflow import LibraryWorkflowController
    from paper_organizer.application.summary_service import ImmediateSummaryController
    from paper_organizer.infra.secrets import default_secret_store
    from paper_organizer.ui.main_window import PaperOrganizerWindow
    from paper_organizer.ui.lifecycle_dialog import LifecyclePreferencesDialog
    from paper_organizer.ui.startup_splash import StartupLoader, create_splash

    start_in_background = "--background" in sys.argv[1:]
    qt_argv = [argument for argument in sys.argv if argument != "--background"]
    app = QApplication.instance() or QApplication(qt_argv)
    from paper_organizer.application.background_ocr import stop_active_ocr_workers

    app.aboutToQuit.connect(stop_active_ocr_workers)
    lifecycle = LifecycleSettingsController()
    was_first_run = lifecycle.first_run_required()
    if was_first_run:
        first_run = LifecyclePreferencesDialog(lifecycle, first_run=True)
        if first_run.exec_() != QDialog.Accepted:
            return 0
    secret_store = default_secret_store()
    ai_settings = AiSettingsController(secret_store)
    summary = ImmediateSummaryController(secret_store)
    workflow = LibraryWorkflowController()
    background_analysis = BackgroundAnalysisService(
        workflow,
        summary,
        secret_store,
    )
    splash = create_splash()
    splash.show()
    app.processEvents()
    runtime: dict[str, object] = {}

    def show_window(snapshot=None, error: str = "") -> None:
        if "window" in runtime:
            return
        window = PaperOrganizerWindow(
            ai_settings,
            summary,
            workflow,
            lifecycle=lifecycle,
            background_analysis=background_analysis,
        )
        if snapshot is not None:
            window.statusBar().showMessage(
                f"JSON {snapshot.local_json_files}개 · 라이브러리 논문 "
                f"{snapshot.library_entries}개 로드 완료"
            )
        elif error:
            window.statusBar().showMessage(f"시작 색인 읽기 경고: {error}")
        runtime["window"] = window
        if start_in_background and window.start_in_background():
            splash.close()
        else:
            window.show()
            splash.finish(window)
            if was_first_run:
                QTimer.singleShot(0, window.show_first_run_ai_setup)

    loader = StartupLoader(workflow, splash)
    runtime["loader"] = loader
    loader.completed.connect(show_window)
    loader.failed.connect(lambda message: show_window(error=message))
    loader.start()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
