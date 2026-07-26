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
            service = GitHubUpdateService("1.1.0")

            with patch(
                "paper_organizer.application.update_service.subprocess.Popen"
            ) as popen:
                service.launch_installer(installer)

            command = popen.call_args.args[0]
            self.assertEqual(command[0], str(installer.resolve()))
            self.assertIn("/CLOSEAPPLICATIONS", command)
            self.assertNotIn("shell", popen.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
