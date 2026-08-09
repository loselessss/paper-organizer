"""Tests for the process-wide serial AI execution queue."""

import time
import unittest
from threading import Event, Lock, Thread

from paper_organizer.application.ai_execution import (
    AI_PRIORITY_BACKGROUND,
    AI_PRIORITY_MANUAL,
    AI_PRIORITY_SEARCH,
    AiExecutionCancelled,
    AiExecutionQueue,
    global_ai_execution_queue,
)
from paper_organizer.application.conversational_search import (
    ConversationalSearchController,
)
from paper_organizer.application.background_analysis import BackgroundAnalysisService
from paper_organizer.application.library_translation import (
    LibraryTranslationService,
)
from paper_organizer.application.ollama_model_manager import (
    OllamaModelManagerService,
)
from paper_organizer.application.selection_ai import SelectionAiService
from paper_organizer.application.summary_service import SummaryController


class NoSecrets:
    def get(self, _provider):
        return None


class MinimalWorkflow:
    pass


class AiExecutionQueueTests(unittest.TestCase):
    def test_fifo_queue_never_runs_two_tasks_at_once(self):
        queue = AiExecutionQueue()
        first_release = Event()
        first_started = Event()
        state_lock = Lock()
        entered = []
        active = 0
        maximum_active = 0

        def run(name):
            nonlocal active, maximum_active
            with queue.slot("test", name):
                with state_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                    entered.append(name)
                if name == "first":
                    first_started.set()
                    first_release.wait(2)
                time.sleep(0.02)
                with state_lock:
                    active -= 1

        first = Thread(target=run, args=("first",))
        second = Thread(target=run, args=("second",))
        third = Thread(target=run, args=("third",))
        first.start()
        self.assertTrue(first_started.wait(1))
        second.start()
        self._wait_for_pending(queue, 1)
        third.start()
        self._wait_for_pending(queue, 2)
        first_release.set()
        for worker in (first, second, third):
            worker.join(2)
            self.assertFalse(worker.is_alive())

        self.assertEqual(entered, ["first", "second", "third"])
        self.assertEqual(maximum_active, 1)

    def test_cancelled_waiter_is_removed_without_running(self):
        queue = AiExecutionQueue()
        release = Event()
        holder_started = Event()
        cancelled = Event()
        errors = []

        def hold():
            with queue.slot("test", "holder"):
                holder_started.set()
                release.wait(2)

        def wait_cancelled():
            try:
                with queue.slot("test", "cancelled", cancel_event=cancelled):
                    self.fail("cancelled task must not run")
            except AiExecutionCancelled as exc:
                errors.append(str(exc))

        holder = Thread(target=hold)
        waiter = Thread(target=wait_cancelled)
        holder.start()
        self.assertTrue(holder_started.wait(1))
        waiter.start()
        self._wait_for_pending(queue, 1)
        cancelled.set()
        waiter.join(2)
        release.set()
        holder.join(2)

        self.assertEqual(len(errors), 1)
        self.assertEqual(queue.snapshot(), (None, ()))

    def test_higher_priority_runs_next_and_equal_priority_stays_fifo(self):
        queue = AiExecutionQueue()
        holder_release = Event()
        holder_started = Event()
        entered = []

        def run(name, priority):
            with queue.slot("test", name, priority=priority):
                entered.append(name)
                if name == "holder":
                    holder_started.set()
                    holder_release.wait(2)

        workers = [
            Thread(target=run, args=("holder", AI_PRIORITY_BACKGROUND)),
            Thread(target=run, args=("background", AI_PRIORITY_BACKGROUND)),
            Thread(target=run, args=("manual", AI_PRIORITY_MANUAL)),
            Thread(target=run, args=("search-first", AI_PRIORITY_SEARCH)),
            Thread(target=run, args=("search-second", AI_PRIORITY_SEARCH)),
        ]
        workers[0].start()
        self.assertTrue(holder_started.wait(1))
        for pending_count, worker in enumerate(workers[1:], start=1):
            worker.start()
            self._wait_for_pending(queue, pending_count)
        holder_release.set()
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())

        self.assertEqual(
            entered,
            ["holder", "search-first", "search-second", "manual", "background"],
        )

    def test_same_thread_can_reenter_without_releasing_the_outer_task(self):
        queue = AiExecutionQueue()
        competitor_entered = Event()

        def compete():
            with queue.slot("test", "competitor"):
                competitor_entered.set()

        with queue.slot("analysis", "paper") as outer:
            with queue.slot("summary", "nested") as nested:
                self.assertEqual(nested, outer)
                competitor = Thread(target=compete)
                competitor.start()
                self._wait_for_pending(queue, 1)
            self.assertFalse(competitor_entered.is_set())
        competitor.join(1)

        self.assertTrue(competitor_entered.is_set())
        self.assertEqual(queue.snapshot(), (None, ()))

    def test_all_ai_services_share_the_global_queue_by_default(self):
        secrets = NoSecrets()
        workflow = MinimalWorkflow()
        expected = global_ai_execution_queue()
        services = (
            SummaryController(secrets),
            LibraryTranslationService(workflow, secrets),
            SelectionAiService(secrets),
            ConversationalSearchController(workflow, secrets),
            OllamaModelManagerService(),
        )
        summary = services[0]
        services += (BackgroundAnalysisService(workflow, summary, secrets),)

        self.assertTrue(
            all(service._execution_queue is expected for service in services)
        )

    def _wait_for_pending(self, queue: AiExecutionQueue, count: int) -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if len(queue.snapshot()[1]) == count:
                return
            time.sleep(0.01)
        self.fail(f"AI queue did not reach {count} pending tasks")


if __name__ == "__main__":
    unittest.main()
