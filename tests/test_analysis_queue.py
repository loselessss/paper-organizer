import tempfile
import unittest
from pathlib import Path

from paper_organizer.application.analysis_queue import (
    AnalysisQueueError,
    AnalysisQueueStore,
)


class AnalysisQueueTests(unittest.TestCase):
    def test_queue_persists_priority_relocation_and_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AnalysisQueueStore(root)
            first = store.enqueue(
                path=root / "first.pdf", file_sha256="a" * 64, title="First"
            )
            second = store.enqueue(
                path=root / "second.pdf", file_sha256="b" * 64, title="Second"
            )
            store.set_priority(second.queue_id, True)
            loaded = AnalysisQueueStore(root).load()
            self.assertEqual([item.queue_id for item in loaded], [second.queue_id, first.queue_id])
            moved = store.relocate(
                "b" * 64,
                root / "library" / "second.pdf",
                status="organized_pending_analysis",
            )
            self.assertEqual(moved.status, "organized_pending_analysis")
            store.remove(first.queue_id)
            self.assertEqual([item.queue_id for item in store.load()], [second.queue_id])

    def test_corrupted_queue_is_reported_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AnalysisQueueStore(Path(temp))
            store.path.parent.mkdir(parents=True)
            store.path.write_text("not json", encoding="utf-8")
            with self.assertRaises(AnalysisQueueError):
                store.load()
            self.assertEqual(store.path.read_text(encoding="utf-8"), "not json")

    def test_claim_completion_retry_and_restart_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AnalysisQueueStore(root)
            queued = store.enqueue(
                path=root / "paper.paperpack",
                file_sha256="c" * 64,
                title="Queued",
                status="organized_pending_analysis",
            )
            claimed = store.claim_next()
            self.assertEqual(claimed.status, "analyzing")
            self.assertEqual(claimed.attempt_count, 1)

            recovered = AnalysisQueueStore(root).recover_interrupted()
            self.assertEqual(recovered, 1)
            self.assertEqual(store.load()[0].status, "organized_pending_analysis")

            claimed = store.claim_next()
            failed = store.mark_failed(claimed.queue_id, "network unavailable")
            self.assertEqual(failed.status, "failed")
            retried = store.retry(queued.queue_id, high=True)
            self.assertEqual(retried.status, "organized_pending_analysis")
            self.assertEqual(retried.priority, 1)
            completed = store.mark_completed(store.claim_next().queue_id)
            self.assertEqual(completed.status, "completed")
            self.assertTrue(completed.completed_at)


if __name__ == "__main__":
    unittest.main()
