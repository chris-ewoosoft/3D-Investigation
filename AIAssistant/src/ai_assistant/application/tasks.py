from __future__ import annotations

import threading
from collections.abc import Mapping

from ..domain.security import DataClassification
from ..domain.tasks import AgentTask, TaskStatus
from ..ports import AgentTaskExecutor, TaskStore


class TaskService:
    """Durable A2A task lifecycle with explicit, observable state changes."""

    def __init__(self, store: TaskStore, executor: AgentTaskExecutor, capabilities: frozenset[str]) -> None:
        self._store = store
        self._executor = executor
        self._capabilities = capabilities

    def submit(self, capability: str, message: str, metadata: Mapping | None = None,
               context_id: str | None = None, classification: DataClassification = DataClassification.INTERNAL) -> AgentTask:
        task = AgentTask.new(capability, message, context_id=context_id, metadata=dict(metadata or {}), classification=classification)
        if capability not in self._capabilities:
            task = task.transition(TaskStatus.REJECTED, error=f"Unsupported capability: {capability}")
            self._store.create(task)
            self._store.append_event(task.id, "status", {"status": task.status, "error": task.error})
            return task
        self._store.create(task)
        self._store.append_event(task.id, "status", {"status": task.status})
        threading.Thread(target=self._run, args=(task.id,), daemon=True, name=f"a2a-{task.id[-8:]}").start()
        return task

    def get(self, task_id: str) -> AgentTask | None:
        return self._store.get(task_id)

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    def events(self, task_id: str) -> tuple[dict, ...]:
        return tuple(self._store.events(task_id))

    def list(self, *, context_id: str | None = None, limit: int = 100) -> tuple[AgentTask, ...]:
        return tuple(self._store.list(context_id=context_id, limit=limit))

    def close(self) -> None:
        close = getattr(self._store, "close", None)
        if callable(close):
            close()

    def cancel(self, task_id: str) -> AgentTask | None:
        task = self._store.get(task_id)
        if task is None or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED, TaskStatus.REJECTED}:
            return None
        cancelled = task.transition(TaskStatus.CANCELED)
        self._store.save(cancelled)
        self._store.append_event(task_id, "status", {"status": cancelled.status})
        return cancelled

    def resume(self, task_id: str, input_data: Mapping) -> AgentTask | None:
        """Resume an explicitly paused task with a durable user or desktop response."""
        task = self._store.get(task_id)
        if task is None or task.status != TaskStatus.INPUT_REQUIRED:
            return None
        continuation = (task.result or {}).get("continuation")
        if not isinstance(continuation, dict):
            return None
        metadata = dict(task.metadata)
        metadata["agent_run.continuation"] = continuation
        metadata["agent_run.resume"] = dict(input_data)
        resumed = task.with_metadata(metadata).transition(TaskStatus.WORKING)
        self._store.save(resumed)
        self._store.append_event(task_id, "status", {"status": resumed.status, "resumed": True})
        threading.Thread(target=self._run, args=(task_id, True), daemon=True, name=f"a2a-{task.id[-8:]}").start()
        return resumed

    def _run(self, task_id: str, already_working: bool = False) -> None:
        task = self._store.get(task_id)
        if task is None or task.status == TaskStatus.CANCELED:
            return
        if already_working:
            working = task
        else:
            working = task.transition(TaskStatus.WORKING)
            self._store.save(working)
            self._store.append_event(task_id, "status", {"status": working.status})
        try:
            result = self._executor(working)
            latest = self._store.get(task_id)
            if latest is None or latest.status == TaskStatus.CANCELED:
                return
            if result.get("status") == "input_required":
                continuation = result.get("continuation")
                if isinstance(continuation, dict):
                    metadata = dict(latest.metadata)
                    metadata["agent_run.continuation"] = continuation
                    metadata.pop("agent_run.resume", None)
                    latest = latest.with_metadata(metadata)
                waiting = latest.transition(TaskStatus.INPUT_REQUIRED, result=result)
                self._store.save(waiting)
                self._store.append_event(task_id, "status", {"status": waiting.status, "result": result})
                return
            completed = latest.transition(TaskStatus.COMPLETED, result=result)
            self._store.save(completed)
            self._store.append_event(task_id, "artifact", {"result": result})
            self._store.append_event(task_id, "status", {"status": completed.status})
        except Exception as error:  # The task becomes observable failure, not a hidden fallback.
            latest = self._store.get(task_id)
            if latest is not None and latest.status != TaskStatus.CANCELED:
                failed = latest.transition(TaskStatus.FAILED, error=str(error))
                self._store.save(failed)
                self._store.append_event(task_id, "status", {"status": failed.status, "error": failed.error})
