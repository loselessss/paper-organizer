import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_organizer.application.update_service import (
    GitHubUpdateService,
    UpdateError,
)


class FakeResponse:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def release_payload(
    *,
    tag: str = "v1.2.0",
    download_url: str = (
        "https://github.com/loselessss/paper-organizer/releases/download/"
        "v1.2.0/PaperOrganizer_Setup_1.2.0.exe"
    ),
    content: bytes = b"installer",
) -> bytes:
    return json.dumps(
        {
            "tag_name": tag,
            "name": "Paper Organizer 1.2.0",
            "body": "Update notes",
            "html_url": (
                "https://github.com/loselessss/paper-organizer/releases/tag/v1.2.0"
            ),
            "published_at": "2026-07-26T00:00:00Z",
            "assets": [
                {
                    "name": "PaperOrganizer_Setup_1.2.0.exe",
                    "browser_download_url": download_url,
                    "size": len(content),
                    "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                }
            ],
        }
    ).encode("utf-8")


class UpdateServiceTests(unittest.TestCase):
    def test_newer_github_release_selects_versioned_installer(self):
        payload = release_payload()
        service = GitHubUpdateService(
            "1.1.0", opener=lambda _request, timeout: FakeResponse(payload)
        )

        update = service.check()

        self.assertIsNotNone(update)
        self.assertEqual(update.version, "1.2.0")
        self.assertEqual(update.release_notes, "Update notes")
        self.assertEqual(
            update.asset.name, "PaperOrganizer_Setup_1.2.0.exe"
        )
        self.assertEqual(len(update.asset.sha256), 64)

    def test_versioned_installer_wins_over_latest_alias(self):
        data = json.loads(release_payload())
        data["assets"].insert(
            0,
            {
                "name": "PaperOrganizer_Setup_latest.exe",
                "browser_download_url": (
                    "https://github.com/loselessss/paper-organizer/releases/"
                    "download/v1.2.0/PaperOrganizer_Setup_latest.exe"
                ),
                "size": 10,
                "digest": f"sha256:{'0' * 64}",
            },
        )
        service = GitHubUpdateService(
            "1.1.0",
            opener=lambda _request, timeout: FakeResponse(
                json.dumps(data).encode("utf-8")
            ),
        )

        update = service.check()

        self.assertEqual(update.asset.name, "PaperOrganizer_Setup_1.2.0.exe")

    def test_mismatched_numbered_installer_is_not_used(self):
        payload = release_payload(tag="v1.3.0")
        service = GitHubUpdateService(
            "1.2.0", opener=lambda _request, timeout: FakeResponse(payload)
        )

        update = service.check()

        self.assertEqual(update.version, "1.3.0")
        self.assertIsNone(update.asset)

    def test_same_or_older_release_does_not_offer_update(self):
        payload = release_payload(tag="v1.1.0")
        service = GitHubUpdateService(
            "1.1.0", opener=lambda _request, timeout: FakeResponse(payload)
        )

        self.assertIsNone(service.check())

    def test_release_asset_must_use_the_repository_github_url(self):
        payload = release_payload(download_url="https://example.com/update.exe")
        service = GitHubUpdateService(
            "1.1.0", opener=lambda _request, timeout: FakeResponse(payload)
        )

        with self.assertRaisesRegex(UpdateError, "안전하지"):
            service.check()

    def test_download_reports_progress_and_verifies_sha256(self):
        content = b"verified installer bytes"
        payload = release_payload(content=content)
        calls = 0

        def opener(_request, timeout):
            nonlocal calls
            calls += 1
            return FakeResponse(payload if calls == 1 else content)

        with tempfile.TemporaryDirectory() as temp:
            service = GitHubUpdateService(
                "1.1.0", opener=opener, download_root=Path(temp)
            )
            update = service.check()
            progress = []

            destination = service.download(update, progress=progress.append)

            self.assertEqual(destination.read_bytes(), content)
            self.assertEqual(progress[-1].completed_bytes, len(content))
            self.assertEqual(progress[-1].total_bytes, len(content))

    def test_download_reuses_verified_cached_installer(self):
        content = b"verified installer bytes"
        payload = release_payload(content=content)
        calls = 0

        def opener(_request, timeout):
            nonlocal calls
            calls += 1
            return FakeResponse(payload)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "PaperOrganizer_Setup_1.2.0.exe"
            destination.write_bytes(content)
            service = GitHubUpdateService(
                "1.1.0", opener=opener, download_root=root
            )
            update = service.check()
            progress = []

            reused = service.download(update, progress=progress.append)

            self.assertEqual(reused, destination)
            self.assertEqual(calls, 1)
            self.assertEqual(progress[-1].completed_bytes, len(content))
            self.assertEqual(progress[-1].bytes_per_second, 0.0)

    def test_download_replaces_invalid_cached_installer(self):
        content = b"verified installer bytes"
        payload = release_payload(content=content)
        calls = 0

        def opener(_request, timeout):
            nonlocal calls
            calls += 1
            return FakeResponse(payload if calls == 1 else content)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "PaperOrganizer_Setup_1.2.0.exe"
            destination.write_bytes(b"broken")
            service = GitHubUpdateService(
                "1.1.0", opener=opener, download_root=root
            )
            update = service.check()

            downloaded = service.download(update)

            self.assertEqual(downloaded.read_bytes(), content)
            self.assertEqual(calls, 2)

    def test_cleanup_keeps_only_latest_future_installer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stale = root / "PaperOrganizer_Setup_1.6.1.exe"
            old_future = root / "PaperOrganizer_Setup_1.7.0.exe"
            newest_future = root / "PaperOrganizer_Setup_1.8.0.exe"
            partial = root / "PaperOrganizer_Setup_1.8.0.exe.part"
            unrelated = root / "keep-me.exe"
            for path in (stale, old_future, newest_future, partial, unrelated):
                path.write_bytes(b"x")
            service = GitHubUpdateService("1.6.1", download_root=root)

            removed = service.cleanup_downloads()

            self.assertEqual(
                {path.name for path in removed},
                {
                    stale.name,
                    old_future.name,
                    partial.name,
                },
            )
            self.assertTrue(newest_future.exists())
            self.assertTrue(unrelated.exists())

    def test_download_removes_partial_file_after_digest_failure(self):
        content = b"verified installer bytes"
        payload = release_payload(content=content)
        calls = 0

        def opener(_request, timeout):
            nonlocal calls
            calls += 1
            return FakeResponse(payload if calls == 1 else b"tampered")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = GitHubUpdateService(
                "1.1.0", opener=opener, download_root=root
            )
            update = service.check()

            with self.assertRaises(UpdateError):
                service.download(update)

            self.assertEqual(list(root.iterdir()), [])

    def test_installer_launch_never_uses_a_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            installer = Path(temp) / "PaperOrganizer_Setup_1.2.0.exe"
            installer.write_bytes(b"MZ")
            service = GitHubUpdateService(
                "1.1.0", signature_verifier=lambda _path: True
            )

            with patch(
                "paper_organizer.application.update_service.subprocess.Popen"
            ) as popen:
                service.launch_installer(installer)

            command = popen.call_args.args[0]
            self.assertEqual(command[0], str(installer.resolve()))
            self.assertIn("/CLOSEAPPLICATIONS", command)
            self.assertNotIn("shell", popen.call_args.kwargs)

    def test_installer_with_untrusted_publisher_is_not_launched(self):
        with tempfile.TemporaryDirectory() as temp:
            installer = Path(temp) / "PaperOrganizer_Setup_1.2.0.exe"
            installer.write_bytes(b"MZ")
            service = GitHubUpdateService(
                "1.1.0", signature_verifier=lambda _path: False
            )
            with patch(
                "paper_organizer.application.update_service.subprocess.Popen"
            ) as popen, self.assertRaisesRegex(UpdateError, "Authenticode"):
                service.launch_installer(installer)
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
