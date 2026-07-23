import tempfile
import unittest
from pathlib import Path

from paper_organizer.application.lifecycle import (
    LifecycleSettingsController,
    LifecycleSettingsError,
    default_startup_command,
)
from paper_organizer.infra.settings import load_settings


class FakeLoginStartup:
    def __init__(self, fail_on: bool | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[bool] = []

    def set_enabled(self, enabled: bool) -> None:
        self.calls.append(enabled)
        if enabled == self.fail_on:
            raise LifecycleSettingsError("simulated registry failure")


class LifecycleSettingsTests(unittest.TestCase):
    def test_first_run_requires_explicit_save_and_persists_choices(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            backend = FakeLoginStartup()
            controller = LifecycleSettingsController(path, backend)
            self.assertTrue(controller.first_run_required())

            saved = controller.save_preferences(
                start_with_windows=True,
                close_behavior="background",
            )

            self.assertTrue(saved.first_run_completed)
            self.assertTrue(saved.start_with_windows)
            self.assertEqual(saved.close_behavior, "background")
            self.assertFalse(controller.first_run_required())
            self.assertEqual(backend.calls, [True])
            self.assertEqual(load_settings(path), saved)

    def test_registry_failure_does_not_complete_first_run(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            backend = FakeLoginStartup(fail_on=True)
            controller = LifecycleSettingsController(path, backend)

            with self.assertRaisesRegex(LifecycleSettingsError, "simulated"):
                controller.save_preferences(
                    start_with_windows=True,
                    close_behavior="quit",
                )

            self.assertTrue(controller.first_run_required())
            self.assertFalse(path.exists())
            self.assertEqual(backend.calls, [True, False])

    def test_close_behavior_must_be_selected(self):
        controller = LifecycleSettingsController(
            Path("unused-settings.json"), FakeLoginStartup()
        )
        with self.assertRaisesRegex(LifecycleSettingsError, "선택"):
            controller.save_preferences(
                start_with_windows=False,
                close_behavior="ask",
            )

    def test_login_command_starts_gui_in_background_mode(self):
        command = default_startup_command()
        self.assertIn("--background", command)
        self.assertTrue(command[0])


if __name__ == "__main__":
    unittest.main()
