"""Guard-path coverage for the durable task lifecycle and task transitions.

``TaskService`` runs work on background threads, so its shutdown and failure
arms only appear under adversarial timing: a join that times out, a resume
with a non-mapping continuation, a store without a ``close`` method, a run
that finds its task cancelled, and an executor that raises after the task
vanished. The frozen ``AgentTask`` transition guards (terminal tasks never
move, nothing returns to ``submitted``) are pinned alongside.
"""
from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from ai_assistant.application.tasks import TaskService
from ai_assistant.domain.errors import TaskCapacityError
from ai_assistant.domain.tasks import AgentTask, TaskStatus


def _store(**overrides):
    store = MagicMock()
    store.get.return_value = None
    for key, value in overrides.items():
        setattr(store, key, value)
    return store


def _working_task(**overrides) -> AgentTask:
    task = AgentTask.new("chat", "hi", **overrides)
    return task.transition(TaskStatus.WORKING)


class TransitionGuardTests(unittest.TestCase):
    def test_a_terminal_task_never_moves(self):
        done = _working_task().transition(TaskStatus.COMPLETED)
        with self.assertRaises(ValueError) as caught:
            done.transition(TaskStatus.FAILED)
        self.assertIn("terminal", str(caught.exception))

    def test_a_task_cannot_return_to_submitted(self):
        with self.assertRaises(ValueError) as caught:
            _working_task().transition(TaskStatus.SUBMITTED)
        self.assertIn("submitted", str(caught.exception))

    def test_metadata_updates_preserve_the_status(self):
        task = _working_task().with_metadata({"agent_run.resume": {}})
        self.assertEqual(task.status, TaskStatus.WORKING)
        self.assertEqual(task.metadata["agent_run.resume"], {})


class CloseTests(unittest.TestCase):
    def test_a_join_timeout_abandons_draining_and_leaves_workers(self):
        """``timeout_seconds=0`` trips the deadline break; the store stays open
        because workers are still registered."""
        blocker = threading.Event()
        created: dict = {}
        store = _store()
        store.commit.side_effect = lambda task, **kwargs: (
            created.setdefault(task.id, task) or True
        )
        store.get.side_effect = lambda task_id: created.get(task_id)
        service = TaskService(store, lambda task: blocker.wait(10), frozenset({"chat"}))
        service.submit("chat", "hi")
        service.close(timeout_seconds=0)
        try:
            self.assertTrue(service._workers)
            self.assertFalse(service._store_closed)
        finally:
            blocker.set()

    def test_close_without_workers_closes_a_closeable_store(self):
        store = _store()
        TaskService(store, lambda task: {}, frozenset({"chat"})).close()
        store.close.assert_called_once_with()

    def test_close_tolerates_a_store_without_close(self):
        """Duck-typed stores are not required to implement ``close``."""
        store = SimpleNamespace(
            commit=lambda task, **kwargs: True, get=lambda task_id: None,
            list=lambda **k: (), events=lambda task_id: (),
        )
        service = TaskService(store, lambda task: {}, frozenset({"chat"}))
        service.close()
        self.assertTrue(service._store_closed)


class ResumeTests(unittest.TestCase):
    def test_a_non_mapping_continuation_refuses_resume(self):
        task = _working_task(
            metadata={"agent_run.continuation": "not-a-mapping"},
        ).transition(TaskStatus.INPUT_REQUIRED)
        service = TaskService(_store(get=MagicMock(return_value=task)), lambda t: {}, frozenset({"chat"}))
        self.assertIsNone(service.resume(task.id, {}))

    def test_a_task_that_is_not_parked_refuses_resume(self):
        service = TaskService(
            _store(get=MagicMock(return_value=_working_task())), lambda t: {}, frozenset({"chat"}),
        )
        self.assertIsNone(service.resume("whatever", {}))


class RunTests(unittest.TestCase):
    def test_a_cancelled_task_is_left_alone(self):
        cancelled = _working_task().transition(TaskStatus.CANCELED)
        store = _store(get=MagicMock(return_value=cancelled))
        executor = MagicMock()
        TaskService(store, executor, frozenset({"chat"}))._run("task-1")
        executor.assert_not_called()
        store.commit.assert_not_called()

    def test_a_vanished_task_after_an_executor_crash_stays_silent(self):
        """The executor raised *and* the task is gone: nothing left to fail."""
        store = _store()
        # read #1: the worker's entry snapshot, read #2: the crash handler's
        # reload. ADR 0032 removed the separate read the compare-and-set probe
        # used to need — the guard is one statement now.
        store.get.side_effect = [_working_task(), None]
        executor = MagicMock(side_effect=RuntimeError("boom"))
        TaskService(store, executor, frozenset({"chat"}))._run("task-1")
        store.commit.assert_called_once()  # the WORKING transition only

    def test_an_executor_crash_marks_the_task_failed(self):
        store = _store()
        task = _working_task()
        store.get.return_value = task
        executor = MagicMock(side_effect=RuntimeError("boom"))
        TaskService(store, executor, frozenset({"chat"}))._run("task-1")
        committed = store.commit.call_args_list[-1].args[0]
        self.assertEqual(committed.status, TaskStatus.FAILED)
        self.assertIn("boom", committed.error)



# ── Durable worker lifecycle (moved from test_coverage_boost3) ──────────

class _MemStore:
    """In-memory TaskStore double supporting the TaskService lifecycle.

    ADR 0032: the store implements the port's single ``commit`` unit of work —
    the state write and the events that announce it land together, and a
    refused compare-and-set writes neither.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, AgentTask] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.closed = False
        self.pruned: list[int] = []

    def commit(self, task: AgentTask, *, expected: TaskStatus | None = None,
               events: tuple[tuple[str, dict[str, Any]], ...] = ()) -> bool:
        with self._lock:
            if expected is not None:
                current = self._tasks.get(task.id)
                if current is None or current.status != expected:
                    return False
            self._tasks[task.id] = task
            for kind, data in events:
                self._events.setdefault(task.id, []).append({"kind": kind, "data": data})
            return True

    def get(self, task_id: str) -> AgentTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def events(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events.get(task_id, []))

    def list(self, *, context_id: str | None = None, limit: int = 100,
             owner: str | None = None) -> list[AgentTask]:
        with self._lock:
            tasks = list(self._tasks.values())
            if context_id:
                tasks = [t for t in tasks if t.context_id == context_id]
            if owner is not None:
                tasks = [t for t in tasks if t.owner == owner]
            return tasks[:limit]

    def close(self) -> None:
        self.closed = True

    def prune(self, retention_days: int) -> int:
        self.pruned.append(retention_days)
        return 2


class _BareStore:
    """A store that predates the pruning contract."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()

class TaskServiceLifecycleTests(unittest.TestCase):
    """End-to-end worker lifecycle for durable A2A tasks."""

    def setUp(self) -> None:
        self.store = _MemStore()
        self.executor = MagicMock()
        self.service = TaskService(
            self.store, self.executor, frozenset({"supervisor", "research"})
        )

    def tearDown(self) -> None:
        self.service.close()

    def test_submit_unsupported_capability_rejected(self) -> None:
        task = self.service.submit("nonexistent", "hello")
        self.assertEqual(task.status, TaskStatus.REJECTED)
        self.assertIn("Unsupported capability", task.error or "")
        events = self.service.events(task.id)
        self.assertEqual(events[-1]["kind"], "status")

    def test_the_event_cursor_is_passed_to_a_store_that_supports_it(self) -> None:
        """The cursor is the store-native sequence.

        A store that offers it must be *asked* for it: the SSE loop then
        reads only the events added since the last page instead of re-reading
        the whole log on every tick.
        """
        seen: list[int] = []

        class _CursorStore(_MemStore):
            def events(self, task_id: str, *, since: int = 0) -> list[dict[str, Any]]:
                seen.append(since)
                return super().events(task_id)

        store = _CursorStore()
        task = AgentTask.new("supervisor", "hello")
        store.commit(task)
        service = TaskService(store, MagicMock(), frozenset({"supervisor"}))
        try:
            service.events(task.id, since=7)
            self.assertEqual(seen, [7])
        finally:
            service.close()

    def test_a_type_error_inside_the_store_is_not_mistaken_for_an_old_signature(self) -> None:
        """A bug in the store must surface — and the store is called once.

        The old-signature fallback used to be detected by catching the
        ``TypeError`` the *call* raised, which also caught a ``TypeError``
        raised **inside** the store and then called the store a second time
        with the old signature.
        """
        calls: list[int] = []

        class _BrokenStore(_MemStore):
            def events(self, task_id: str, *, since: int = 0) -> list[dict[str, Any]]:
                calls.append(since)
                raise TypeError

        store = _BrokenStore()
        task = AgentTask.new("supervisor", "hello")
        store.commit(task)
        service = TaskService(store, MagicMock(), frozenset({"supervisor"}))
        try:
            with self.assertRaises(TypeError):
                service.events(task.id)
            self.assertEqual(calls, [0])
        finally:
            service.close()

    def test_submit_to_completed(self) -> None:
        self.executor.return_value = {"status": "completed", "content": "Done"}
        task = self.service.submit("supervisor", "hello")
        self.assertEqual(task.status, TaskStatus.SUBMITTED)
        self.assertTrue(
            _wait_until(lambda: self.service.get(task.id).status == TaskStatus.COMPLETED)
        )
        self.assertEqual(self.service.get(task.id).result["content"], "Done")
        kinds = [event["kind"] for event in self.service.events(task.id)]
        self.assertIn("artifact", kinds)

    def test_executor_failure_becomes_observable(self) -> None:
        self.executor.side_effect = RuntimeError("backend exploded")
        task = self.service.submit("research", "query")
        self.assertTrue(
            _wait_until(lambda: self.service.get(task.id).status == TaskStatus.FAILED)
        )
        self.assertIn("backend exploded", self.service.get(task.id).error or "")

    def test_input_required_then_resume(self) -> None:
        first = {"status": "input_required", "continuation": {"step": 2}, "content": "Ask AI"}
        second = {"status": "completed", "content": "After approval"}
        self.executor.side_effect = [first, second]

        task = self.service.submit("supervisor", "apply patch")
        self.assertTrue(
            _wait_until(lambda: self.service.get(task.id).status == TaskStatus.INPUT_REQUIRED)
        )
        waiting = self.service.get(task.id)
        self.assertEqual(waiting.metadata["agent_run.continuation"], {"step": 2})

        resumed = self.service.resume(task.id, {"approved": True})
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.status, TaskStatus.WORKING)
        self.assertTrue(
            _wait_until(lambda: self.service.get(task.id).status == TaskStatus.COMPLETED)
        )
        self.assertEqual(self.service.get(task.id).result["content"], "After approval")

    def test_resume_invalid_state_returns_none(self) -> None:
        task = self.service.submit("supervisor", "hello")
        self.assertTrue(
            _wait_until(lambda: self.service.get(task.id).status == TaskStatus.COMPLETED)
        )
        self.assertIsNone(self.service.resume(task.id, {"input": 1}))
        self.assertIsNone(self.service.resume("missing-task", {"input": 1}))

    def test_cancel_running_task(self) -> None:
        self.executor.side_effect = lambda task: time.sleep(1.0) or {"status": "completed"}
        task = self.service.submit("supervisor", "long task")
        cancelled = self.service.cancel(task.id)
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.status, TaskStatus.CANCELED)
        # Terminal cancellation means the late executor result is ignored.
        self.assertTrue(
            _wait_until(lambda: self.service.get(task.id).status == TaskStatus.CANCELED)
        )

    def test_cancel_unknown_or_terminal_returns_none(self) -> None:
        self.assertIsNone(self.service.cancel("missing"))
        self.executor.return_value = {"status": "completed"}
        task = self.service.submit("supervisor", "hello")
        self.assertTrue(
            _wait_until(lambda: self.service.get(task.id).status == TaskStatus.COMPLETED)
        )
        self.assertIsNone(self.service.cancel(task.id))

    def test_close_drains_workers_and_closes_store(self) -> None:
        self.executor.side_effect = lambda task: time.sleep(0.4) or {"status": "completed"}
        task = self.service.submit("supervisor", "slow")
        self.service.close(timeout_seconds=2.0)
        self.assertTrue(self.store.closed)
        self.assertTrue(
            _wait_until(lambda: self.service.get(task.id).status == TaskStatus.COMPLETED)
        )

    def test_submit_after_close_raises(self) -> None:
        self.service.close()
        with self.assertRaises(RuntimeError):
            self.service.submit("supervisor", "too late")

    def test_list_and_context_filtering(self) -> None:
        self.executor.return_value = {"status": "completed"}
        self.service.submit("supervisor", "hello", context_id="ctx-1")
        self.service.submit("research", "query", context_id="ctx-2")
        all_tasks = self.service.list()
        self.assertEqual(len(all_tasks), 2)
        only_ctx1 = self.service.list(context_id="ctx-1")
        self.assertEqual(len(only_ctx1), 1)
        self.assertEqual(only_ctx1[0].context_id, "ctx-1")

    def test_task_ownership_scopes_every_operation(self) -> None:
        self.executor.side_effect = lambda task: time.sleep(0.5) or {"status": "completed"}
        task = self.service.submit("supervisor", "hello", owner="alpha")
        self.assertEqual(task.owner, "alpha")
        self.assertEqual(task.with_metadata({"seen": True}).owner, "alpha")

        self.assertIsNone(self.service.get(task.id, owner="beta"))
        self.assertEqual(self.service.events(task.id, owner="beta"), ())
        self.assertIsNone(self.service.cancel(task.id, owner="beta"))
        self.assertIsNone(self.service.resume(task.id, {"approved": True}, owner="beta"))
        self.assertEqual(self.service.list(owner="beta"), ())
        self.assertEqual([item.id for item in self.service.list(owner="alpha")], [task.id])

        self.assertIsNotNone(self.service.cancel(task.id, owner="alpha"))

    def test_prune_delegates_and_zero_disables_the_window(self) -> None:
        self.assertEqual(self.service.prune(7), 2)
        self.assertEqual(self.store.pruned, [7])
        self.assertEqual(self.service.prune(0), 0)
        self.assertEqual(self.store.pruned, [7])

    def test_prune_is_a_noop_for_stores_without_the_contract(self) -> None:
        store = _BareStore()
        service = TaskService(store, self.executor, frozenset({"supervisor"}))
        try:
            self.assertEqual(service.prune(30), 0)
        finally:
            service.close()
        self.assertTrue(store.closed)


class TaskAdmissionTests(unittest.TestCase):
    """ADR 0041: a burst is refused before a worker thread is created.

    The executor's own run limit answers only after the task thread exists, so
    these tests pin the *admission* point: the thread count must never exceed
    the configured bound, and the refusal must be durable and observable.
    """

    def test_submit_at_capacity_is_rejected_without_creating_a_thread(self) -> None:
        store = _MemStore()
        started = threading.Event()
        unblock = threading.Event()

        def executor(task: AgentTask) -> dict[str, Any]:
            started.set()
            unblock.wait(5)
            return {"status": "completed"}

        service = TaskService(store, executor, frozenset({"supervisor"}),
                              max_concurrent_tasks=1)
        try:
            first = service.submit("supervisor", "first")
            self.assertTrue(started.wait(3))
            second = service.submit("supervisor", "second")

            self.assertEqual(first.status, TaskStatus.SUBMITTED)
            self.assertEqual(second.status, TaskStatus.REJECTED)
            self.assertIn("capacity", second.error or "")
            # The refused task was never started: exactly one worker exists.
            self.assertEqual(len(service._workers), 1)
            # The refusal is durable and carries the event that explains it.
            self.assertEqual(service.get(second.id).status, TaskStatus.REJECTED)
            events = service.events(second.id)
            self.assertEqual(events[-1]["kind"], "status")
            self.assertIn("capacity", events[-1]["data"]["error"])

            # Finishing the run returns the slot; the next submit is admitted.
            unblock.set()
            self.assertTrue(_wait_until(
                lambda: service.get(first.id).status == TaskStatus.COMPLETED))
            third = service.submit("supervisor", "third")
            self.assertEqual(third.status, TaskStatus.SUBMITTED)
        finally:
            unblock.set()
            service.close()

    def test_resume_at_capacity_raises_instead_of_claiming_not_resumable(self) -> None:
        store = _MemStore()
        blocked = threading.Event()
        unblock = threading.Event()
        park_runs = 0

        def executor(task: AgentTask) -> dict[str, Any]:
            nonlocal park_runs
            if task.message == "park":
                park_runs += 1
                if park_runs == 1:
                    return {"status": "input_required", "continuation": {"step": 2}}
                return {"status": "completed"}
            if task.message == "block":
                blocked.set()
                unblock.wait(5)
            return {"status": "completed"}

        service = TaskService(store, executor, frozenset({"supervisor"}),
                              max_concurrent_tasks=1)
        try:
            parked = service.submit("supervisor", "park")
            self.assertTrue(_wait_until(
                lambda: service.get(parked.id).status == TaskStatus.INPUT_REQUIRED))
            service.submit("supervisor", "block")
            self.assertTrue(blocked.wait(3))

            with self.assertRaises(TaskCapacityError):
                service.resume(parked.id, {"approved": True})
            # The refusal did not consume the parked task.
            self.assertEqual(service.get(parked.id).status, TaskStatus.INPUT_REQUIRED)

            unblock.set()
            self.assertTrue(_wait_until(
                lambda: len(service._workers) == 0))
            resumed = service.resume(parked.id, {"approved": True})
            self.assertIsNotNone(resumed)
            self.assertEqual(resumed.status, TaskStatus.WORKING)
            self.assertTrue(_wait_until(
                lambda: service.get(parked.id).status == TaskStatus.COMPLETED))
        finally:
            unblock.set()
            service.close()

    def test_a_resume_after_close_releases_its_slot(self) -> None:
        """``_start`` refuses while closing; the reserved slot must go back."""
        store = _MemStore()

        def executor(task: AgentTask) -> dict[str, Any]:
            return {"status": "input_required", "continuation": {"step": 2}}

        service = TaskService(store, executor, frozenset({"supervisor"}),
                              max_concurrent_tasks=1)
        parked = service.submit("supervisor", "park")
        self.assertTrue(_wait_until(
            lambda: service.get(parked.id).status == TaskStatus.INPUT_REQUIRED))
        service.close()
        with self.assertRaises(RuntimeError):
            service.resume(parked.id, {"approved": True})
        # The slot was returned, so a fresh service over the same store can
        # still admit work (a leaked slot would be invisible until exhaustion).
        self.assertEqual(service._slots._value, 1)  # type: ignore[attr-defined]

if __name__ == "__main__":
    unittest.main()
