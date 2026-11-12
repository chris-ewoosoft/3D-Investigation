"""Agent HTTP routes: /v1/agent/execute, /v1/agent/cancel, /v1/agent/ui-action-result, /v1/agent/approve.

Extracted from legacy agent_module.py into clean HTTP adapter. This adapter owns
transport negotiation (JSON vs Server-Sent Events); the agent service itself is
transport-agnostic and returns a plain result while optionally streaming steps
through an ``event_sink`` callback.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Iterable
from functools import partial
from typing import TYPE_CHECKING, Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ai_assistant.agents.models import (
    AgentApproveRequest,
    AgentCancelRequest,
    AgentExecuteRequest,
    AgentUiActionResultRequest,
)
from ai_assistant.domain.memory import DEFAULT_MEMORY_OWNER

from .security import TransportPolicy, agent_guards

if TYPE_CHECKING:
    from ai_assistant.agents.service import AgentService


class AgentStreamWorkerTracker:
    """Own every HTTP SSE worker and drain it before persistence closes.

    Starlette owns the response generator, but the agent run itself executes
    on a plain thread.  A detached daemon thread can outlive the response and
    race the lifespan while it closes the checkpointer and memory stores.  The
    composition root therefore owns one tracker, rejects new work once
    shutdown begins, cancels every active session, and joins the workers with
    one bounded deadline.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._workers: dict[threading.Thread, Callable[[], None]] = {}
        self._closing = False

    def start(self, target: Callable[[], None], cancel: Callable[[], None]) -> bool:
        """Start and register one worker, or refuse after shutdown begins."""
        holder: dict[str, threading.Thread] = {}

        def tracked() -> None:
            try:
                target()
            finally:
                with self._lock:
                    self._workers.pop(holder["thread"], None)

        worker = threading.Thread(target=tracked, daemon=True, name="agent-sse")
        holder["thread"] = worker
        with self._lock:
            if self._closing:
                return False
            self._workers[worker] = cancel
        try:
            worker.start()
        except BaseException:
            with self._lock:
                self._workers.pop(worker, None)
            raise
        return True

    def close(self, timeout_seconds: float = 5.0) -> bool:
        """Cancel active runs and wait at most ``timeout_seconds`` for them.

        ``False`` means at least one worker is still using the runtime.  The
        lifespan then deliberately leaves its stores open for process teardown
        instead of closing them underneath live code.
        """
        with self._lock:
            self._closing = True
            workers = tuple(self._workers.items())
        for _worker, cancel in workers:
            try:
                cancel()
            except Exception:
                # A run may finish between the snapshot and cancellation.
                pass
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        for worker, _cancel in workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)
        with self._lock:
            return not self._workers


def _cancel_agent_run(agent_service: "AgentService", session_id: str) -> None:
    """Best-effort cooperative cancel when the SSE client disconnects.

    The run is already on a worker thread; without this a dropped browser/Qt
    connection leaves it consuming the local model until completion.
    """
    try:
        agent_service.cancel(AgentCancelRequest(session_id=session_id or "agent_default"))
    except Exception:
        # The run may have finished between the disconnect and this callback.
        pass


def _sse_events(events: queue.Queue[tuple[str, object]],
                on_disconnect: Callable[[], None] | None = None) -> Iterable[str]:
    """Turn worker events into SSE frames and cancel a dropped run.

    Kept separate from the thread/response wiring so the disconnect path is
    testable without an HTTP transport: closing this generator before a
    ``result``/``error`` frame is exactly what Starlette does when the client
    drops the connection.
    """
    finished = False
    try:
        yield 'event: status\ndata: {"status": "running"}\n\n'
        while True:
            kind, value = events.get()
            if kind == "step":
                yield f"event: step\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
            elif kind == "result":
                finished = True
                yield f"event: done\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"
                return
            else:
                finished = True
                yield f"event: error\ndata: {json.dumps({'detail': value}, ensure_ascii=False)}\n\n"
                return
    finally:
        if not finished and on_disconnect is not None:
            on_disconnect()


def _stream_langgraph_execution(
    run: Callable[[Callable[[dict[str, Any]], None]], dict[str, Any]],
    on_disconnect: Callable[[], None] | None = None,
    worker_tracker: AgentStreamWorkerTracker | None = None,
) -> StreamingResponse:
    """Stream agent node/tool steps as they happen over SSE."""
    events: queue.Queue[tuple[str, object]] = queue.Queue()

    def worker() -> None:
        try:
            events.put(("result", run(lambda step: events.put(("step", step)))))
        except BaseException as error:  # Stream worker boundary must always unblock _sse_events.
            events.put(("error", str(error) or type(error).__name__))

    tracker = worker_tracker or AgentStreamWorkerTracker()
    cancel = on_disconnect or (lambda: None)
    if not tracker.start(worker, cancel):
        events.put(("error", "Agent service is shutting down."))

    return StreamingResponse(
        _sse_events(events, on_disconnect),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def build_agent_router(agent_service: "AgentService | None" = None,
                       policy: TransportPolicy | None = None,
                       worker_tracker: AgentStreamWorkerTracker | None = None) -> APIRouter:
    """Create the /v1/agent/* APIRouter.

    Parameters
    ----------
    agent_service: The injected agent service instance. The composition root
                   builds one ``AgentService`` with the shared tool gateway and
                   passes it here; a bare default is only used by local tooling.
    policy:        Transport trust policy (ADR 0048) — loopback peer, allowed
                   origin and mandatory ``AI_AGENT_TOKEN`` bearer.
    """
    if agent_service is None:
        from ai_assistant.agents.service import AgentService
        agent_service = AgentService()
    streams = worker_tracker or AgentStreamWorkerTracker()

    router = APIRouter(tags=["agent"], dependencies=agent_guards(policy))

    def _client_host(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @router.post("/v1/agent/execute", response_model=None)
    def agent_execute(request: AgentExecuteRequest, http_req: Request) -> dict[str, Any] | StreamingResponse:
        """Execute an agentic task with tool-calling loop (JSON or SSE).

        This surface is loopback-only (``agent_guards(allow_remote=False)``),
        so every run belongs to the local desktop install and therefore to
        ``DEFAULT_MEMORY_OWNER``. A multi-tenant caller enters through A2A,
        where the task's tenant scopes the memory instead (ADR 0043).
        """
        client_host = _client_host(http_req)
        if "text/event-stream" in http_req.headers.get("accept", ""):
            return _stream_langgraph_execution(
                lambda sink: agent_service.execute(
                    request, owner=DEFAULT_MEMORY_OWNER,
                    event_sink=sink, client_host=client_host,
                ),
                on_disconnect=partial(_cancel_agent_run, agent_service, request.session_id),
                worker_tracker=streams,
            )
        return agent_service.execute(
            request, owner=DEFAULT_MEMORY_OWNER, client_host=client_host,
        )

    @router.post("/v1/agent/cancel")
    def agent_cancel(request: AgentCancelRequest) -> dict[str, Any]:
        """Request cooperative cancellation for a running session/request."""
        return agent_service.cancel(request)

    @router.post("/v1/agent/ui-action-result")
    def agent_ui_action_result(request: AgentUiActionResultRequest) -> dict[str, Any]:
        """Close the desktop-action loop after the Qt slot has run."""
        return agent_service.ui_action_result(request)

    @router.post("/v1/agent/approve")
    def agent_approve(request: AgentApproveRequest) -> dict[str, Any]:
        """Approve or reject a pending agent action (write_file, run_command)."""
        return agent_service.approve(request)

    return router


__all__ = [
    "AgentStreamWorkerTracker",
    "build_agent_router",
]
