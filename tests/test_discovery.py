import os
import tempfile
import time
import unittest
from pathlib import Path

from paper_organizer.core.discovery import DiscoveryTracker


class DiscoveryTests(unittest.TestCase):
    def _old_file(self, path: Path, data: bytes) -> None:
        path.write_bytes(data)
        old = time.time() - 120
        os.utime(path, (old, old))

    def test_requires_two_unchanged_scans(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "paper.pdf"
            self._old_file(pdf, b"%PDF-1.7\nplaceholder")
            tracker = DiscoveryTracker()
            self.assertEqual(tracker.scan(root), [])
            stable = tracker.scan(root)
            self.assertEqual([item.path for item in stable], [pdf])

    def test_manual_scan_can_accept_first_stable_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "paper.pdf"
            self._old_file(pdf, b"%PDF-1.7\nplaceholder")
            tracker = DiscoveryTracker()

            stable = tracker.scan(root, require_previous_observation=False)

            self.assertEqual([item.path for item in stable], [pdf])

    def test_ignores_partial_and_fake_pdfs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partial = root / "partial.pdf"
            fake = root / "fake.pdf"
            self._old_file(partial, b"%PDF-1.7\nplaceholder")
            self._old_file(root / "partial.pdf.crdownload", b"downloading")
            self._old_file(fake, b"not a pdf")
            tracker = DiscoveryTracker()
            tracker.scan(root)
            self.assertEqual(tracker.scan(root), [])

    def test_changed_size_resets_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "paper.pdf"
            self._old_file(pdf, b"%PDF-1.7\nfirst")
            tracker = DiscoveryTracker()
            tracker.scan(root)
            self._old_file(pdf, b"%PDF-1.7\nfirst and more")
            self.assertEqual(tracker.scan(root), [])
            self.assertEqual([item.path for item in tracker.scan(root)], [pdf])


if __name__ == "__main__":
    unittest.main()
