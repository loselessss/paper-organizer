"""Serialize AI inference through one stable priority execution queue."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition, Event, get_ident
from typing import Iterator
from uuid import uuid4


AI_PRIORITY_BACKGROUND = 0
AI_PRIORITY_MODEL_VERIFICATION = 40
AI_PRIORITY_DEFAULT = 50
AI_PRIORITY_MANUAL = 60
AI_PRIORITY_SELECTION = 80
AI_PRIORITY_SEARCH = 100


class AiExecutionCancelled(RuntimeError):
    """Raised when a task is cancelled before it acquires the AI runtime."""


@dataclass(frozen=True, slots=True)
class AiExecutionTask:
    task_id: str
    task_type: str
    title: str
    priority: int


class AiExecutionLease:
    """Release one acquired (possibly re-entrant) queue slot exactly once."""

    def __init__(self, queue: "AiExecutionQueue", task: AiExecutionTask) -> None:
        self._queue = queue
        self.task = task
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._queue._release(self.task)

    def __enter__(self) -> AiExecutionTask:
        return self.task

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


class AiExecutionQueue:
    """Provide a non-preemptive, stable-priority gate for every AI request."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._waiting: deque[AiExecutionTask] = deque()
        self._active: AiExecutionTask | None = None
        self._owner_thread: int | None = None
        self._depth = 0

    def acquire(
        self,
        task_type: str,
        title: str,
        *,
        priority: int = AI_PRIORITY_DEFAULT,
        cancel_event: Event | None = None,
    ) -> AiExecutionLease:
        owner = get_ident()
        with self._condition:
            if self._active is not None and self._owner_thread == owner:
                self._depth += 1
                return AiExecutionLease(self, self._active)
            task = AiExecutionTask(
                uuid4().hex,
                task_type,
                title.strip() or task_type,
                int(priority),
            )
            self._waiting.append(task)
            while self._active is not None or self._next_waiting() != task:
                if cancel_event is not None and cancel_event.is_set():
                    self._waiting.remove(task)
                    self._condition.notify_all()
                    raise AiExecutionCancelled("대기 중인 AI 작업이 취소됐습니다.")
                self._condition.wait(0.1)
            self._waiting.remove(task)
            self._active = task
            self._owner_thread = owner
            self._depth = 1
            return AiExecutionLease(self, task)

    @contextmanager
    def slot(
        self,
        task_type: str,
        title: str,
        *,
        priority: int = AI_PRIORITY_DEFAULT,
        cancel_event: Event | None = None,
    ) -> Iterator[AiExecutionTask]:
        with self.acquire(
            task_type,
            title,
            priority=priority,
            cancel_event=cancel_event,
        ) as task:
            yield task

    def snapshot(self) -> tuple[AiExecutionTask | None, tuple[AiExecutionTask, ...]]:
        with self._condition:
            return self._active, tuple(self._waiting)

    def _next_waiting(self) -> AiExecutionTask | None:
        if not self._waiting:
            return None
        return max(self._waiting, key=lambda task: task.priority)

    def _release(self, task: AiExecutionTask) -> None:
        with self._condition:
            if self._active != task or self._owner_thread != get_ident():
                raise RuntimeError("AI 실행 큐를 획득한 스레드에서만 해제할 수 있습니다.")
            self._depth -= 1
            if self._depth == 0:
                self._active = None
                self._owner_thread = None
                self._condition.notify_all()


_GLOBAL_AI_EXECUTION_QUEUE = AiExecutionQueue()


def global_ai_execution_queue() -> AiExecutionQueue:
    """Return the single AI execution queue shared by the desktop process."""

    return _GLOBAL_AI_EXECUTION_QUEUE
