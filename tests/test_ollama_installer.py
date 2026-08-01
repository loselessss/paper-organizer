import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paper_organizer.infra.ollama_installer import (
    OLLAMA_DOWNLOAD_URL,
    WINGET_PACKAGE_ID,
    ensure_runtime,
    find_ollama_app_executable,
    find_winget_executable,
    inspect_runtime,
    restart_runtime,
    start_runtime,
    stop_managed_runtime,
)
from paper_organizer.infra.ollama_runtime import OllamaRuntimeStatus


class FakeInspector:
    def __init__(self, *states):
        self._states = list(states)

    def inspect(self, timeout: float = 1.5):
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]


def running(version: str = "0.5.0") -> OllamaRuntimeStatus:
    return OllamaRuntimeStatus(reachable=True, version=version, models=())


def stopped() -> OllamaRuntimeStatus:
    return OllamaRuntimeStatus(False, "", (), "connection refused")


def completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["winget"], returncode=returncode, stdout="", stderr=stderr
    )


class InspectRuntimeTests(unittest.TestCase):
    def test_running_runtime_is_reported_as_installed(self):
        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="C:/ollama.exe",
        ):
            state = inspect_runtime(FakeInspector(running()))
        self.assertTrue(state.installed)
        self.assertTrue(state.running)
        self.assertIn("실행 중", state.message)

    def test_missing_executable_and_server_means_not_installed(self):
        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="",
        ):
            state = inspect_runtime(FakeInspector(stopped()))
        self.assertFalse(state.installed)
        self.assertFalse(state.running)
        self.assertIn("설치되어 있지 않습니다", state.message)


class FindWingetTests(unittest.TestCase):
    def test_path_lookup_wins_when_available(self):
        with mock.patch(
            "paper_organizer.infra.ollama_installer.shutil.which",
            return_value="C:/Windows/winget.exe",
        ):
            self.assertEqual(find_winget_executable(), "C:/Windows/winget.exe")

    def test_store_alias_folder_is_used_when_path_misses_it(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp)
            alias = local / "Microsoft" / "WindowsApps"
            alias.mkdir(parents=True)
            (alias / "winget.exe").write_bytes(b"")
            with mock.patch(
                "paper_organizer.infra.ollama_installer.shutil.which",
                return_value=None,
            ), mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                self.assertEqual(
                    find_winget_executable(), str(alias / "winget.exe")
                )

    def test_missing_everywhere_returns_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch(
                "paper_organizer.infra.ollama_installer.shutil.which",
                return_value=None,
            ), mock.patch.dict(os.environ, {"LOCALAPPDATA": temp}):
                self.assertEqual(find_winget_executable(), "")

    def test_inaccessible_store_alias_returns_empty(self):
        with mock.patch(
            "paper_organizer.infra.ollama_installer.shutil.which",
            return_value=None,
        ), mock.patch(
            "paper_organizer.infra.ollama_installer.Path.is_file",
            side_effect=OSError("access denied"),
        ):
            self.assertEqual(find_winget_executable(), "")


class FindOllamaAppTests(unittest.TestCase):
    def test_desktop_app_is_found_next_to_the_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            cli = Path(temp) / "ollama.exe"
            app = Path(temp) / "ollama app.exe"
            cli.write_bytes(b"")
            app.write_bytes(b"")
            with mock.patch(
                "paper_organizer.infra.ollama_installer.find_ollama_executable",
                return_value=str(cli),
            ):
                self.assertEqual(find_ollama_app_executable(), str(app))


class ManagedRuntimeTests(unittest.TestCase):
    def test_stopped_runtime_starts_on_the_loopback_endpoint(self):
        process = mock.Mock()
        process.poll.return_value = None
        inspector = FakeInspector(stopped(), running())
        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="C:/ollama.exe",
        ), mock.patch(
            "paper_organizer.infra.ollama_installer.subprocess.Popen",
            return_value=process,
        ) as popen:
            self.assertTrue(start_runtime(inspector=inspector, sleep=lambda _: None))

        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(command, ["C:/ollama.exe", "serve"])
        self.assertEqual(environment["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(
            popen.call_args.kwargs["creationflags"],
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_runtime_that_exits_during_startup_fails_immediately(self):
        process = mock.Mock()
        process.poll.return_value = 1
        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="C:/ollama.exe",
        ), mock.patch(
            "paper_organizer.infra.ollama_installer.subprocess.Popen",
            return_value=process,
        ):
            self.assertFalse(
                start_runtime(
                    inspector=FakeInspector(stopped()),
                    sleep=lambda _: None,
                )
            )

    def test_only_tracked_runtime_process_is_stopped(self):
        process = mock.Mock()
        process.poll.return_value = None
        with mock.patch(
            "paper_organizer.infra.ollama_installer._managed_process", process
        ):
            self.assertTrue(stop_managed_runtime())
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)

    def test_restart_replaces_tray_and_server_with_hidden_server(self):
        commands = []
        inspector = FakeInspector(stopped())
        with (
            mock.patch(
                "paper_organizer.infra.ollama_installer.find_ollama_executable",
                return_value="C:/ollama.exe",
            ),
            mock.patch(
                "paper_organizer.infra.ollama_installer.stop_managed_runtime",
                return_value=False,
            ),
            mock.patch(
                "paper_organizer.infra.ollama_installer.start_runtime",
                return_value=True,
            ) as start,
            mock.patch(
                "paper_organizer.infra.ollama_installer.subprocess.Popen"
            ) as popen,
            mock.patch(
                "paper_organizer.infra.ollama_installer.sanitized_child_environment"
            ) as child_environment,
            mock.patch(
                "paper_organizer.infra.ollama_installer.find_ollama_app_executable"
            ) as find_app,
            mock.patch(
                "paper_organizer.infra.ollama_installer._managed_process", None
            ),
            mock.patch.dict(
                os.environ,
                {
                    "OLLAMA_IGPU_ENABLE": "1",
                    "OPENAI_API_KEY": "should-not-reach-child",
                },
            ),
        ):
            result = restart_runtime(
                inspector=inspector,
                run_command=lambda command, timeout: (
                    commands.append((tuple(command), timeout)) or completed(0)
                ),
                sleep=lambda _seconds: None,
            )

        self.assertTrue(result)
        self.assertEqual(
            [command[0][2] for command in commands],
            ["ollama app.exe", "ollama.exe"],
        )
        start.assert_called_once_with(
            inspector=inspector,
            timeout_seconds=60,
            sleep=mock.ANY,
        )
        find_app.assert_not_called()
        popen.assert_not_called()
        child_environment.assert_not_called()


class EnsureRuntimeTests(unittest.TestCase):
    def test_already_running_runtime_installs_nothing(self):
        calls = []

        def runner(command, timeout):
            calls.append(command)
            return completed(0)

        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="C:/ollama.exe",
        ):
            result = ensure_runtime(
                allow_install=True,
                inspector=FakeInspector(running()),
                run_command=runner,
                start=lambda: True,
            )
        self.assertTrue(result.ok)
        self.assertEqual(calls, [])

    def test_installed_but_stopped_runtime_is_only_started(self):
        started = []

        def runner(command, timeout):
            raise AssertionError("설치를 시도하면 안 됩니다")

        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="C:/ollama.exe",
        ):
            result = ensure_runtime(
                allow_install=True,
                inspector=FakeInspector(stopped(), stopped(), running()),
                run_command=runner,
                start=lambda: bool(started.append(True) or True),
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(started), 1)
        self.assertIn("실행했습니다", result.message)

    def test_missing_runtime_is_not_installed_without_consent(self):
        def runner(command, timeout):
            raise AssertionError("동의 없이 설치하면 안 됩니다")

        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="",
        ):
            result = ensure_runtime(
                allow_install=False,
                inspector=FakeInspector(stopped()),
                run_command=runner,
                start=lambda: False,
            )
        self.assertFalse(result.ok)
        self.assertTrue(result.needs_manual_download)

    def test_consented_install_uses_winget_and_then_starts(self):
        calls = []

        def runner(command, timeout):
            calls.append(list(command))
            return completed(0)

        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="",
        ), mock.patch(
            "paper_organizer.infra.ollama_installer.find_winget_executable",
            return_value="C:/winget.exe",
        ):
            result = ensure_runtime(
                allow_install=True,
                inspector=FakeInspector(stopped(), stopped(), running()),
                run_command=runner,
                start=lambda: True,
            )
        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 1)
        self.assertIn(WINGET_PACKAGE_ID, calls[0])
        self.assertIn("--silent", calls[0])

    def test_winget_failure_points_at_the_official_download(self):
        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="",
        ), mock.patch(
            "paper_organizer.infra.ollama_installer.find_winget_executable",
            return_value="C:/winget.exe",
        ):
            result = ensure_runtime(
                allow_install=True,
                inspector=FakeInspector(stopped()),
                run_command=lambda command, timeout: completed(1, "network error"),
                start=lambda: False,
            )
        self.assertFalse(result.ok)
        self.assertTrue(result.needs_manual_download)
        self.assertIn(OLLAMA_DOWNLOAD_URL, result.message)
        self.assertIn("network error", result.message)

    def test_missing_winget_falls_back_to_manual_download(self):
        with mock.patch(
            "paper_organizer.infra.ollama_installer.find_ollama_executable",
            return_value="",
        ), mock.patch(
            "paper_organizer.infra.ollama_installer.find_winget_executable",
            return_value="",
        ):
            result = ensure_runtime(
                allow_install=True,
                inspector=FakeInspector(stopped()),
                run_command=lambda command, timeout: completed(0),
                start=lambda: False,
            )
        self.assertFalse(result.ok)
        self.assertTrue(result.needs_manual_download)
        self.assertIn(OLLAMA_DOWNLOAD_URL, result.message)


if __name__ == "__main__":
    unittest.main()
