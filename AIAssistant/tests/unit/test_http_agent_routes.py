"""Unit tests for the /v1/agent/* HTTP router.

The router is transport-only: a fake ``AgentService`` records the calls and
returns canned results, so no LangGraph infrastructure is needed.
"""
from __future__ import annotations

import queue
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_assistant.adapters.http.agent_routes import (
    AgentStreamWorkerTracker,
    _cancel_agent_run,
    _sse_events,
    _stream_langgraph_execution,
    build_agent_router,
)
from ai_assistant.adapters.http.security import TransportPolicy
from ai_assistant.domain.memory import DEFAULT_MEMORY_OWNER


class _FakeAgentService:
    def __init__(self, execute_result=None, execute_error: Exception | None = None) -> None:
        self.execute_result = execute_result or {"status": "ok", "result": "hello"}
        self.execute_error = execute_error
        self.calls: list[tuple[str, object]] = []

    def execute(self, request, *, owner=None, event_sink=None, client_host=None):
        self.calls.append(("execute", (request, owner, event_sink, client_host)))
        if self.execute_error is not None:
            raise self.execute_error
        if event_sink is not None:
            event_sink({"node": "tools"})
        return self.execute_result

    def cancel(self, request):
        self.calls.append(("cancel", request))
        return {"status": "cancelled"}

    def ui_action_result(self, request):
        self.calls.append(("ui_action_result", request))
        return {"status": "ok"}

    def approve(self, request):
        self.calls.append(("approve", request))
        return {"status": "approved"}


def _build_client(service: _FakeAgentService | None = None) -> tuple[TestClient, _FakeAgentService]:
    service = service or _FakeAgentService()
    app = FastAPI()
    app.include_router(build_agent_router(service, TransportPolicy(agent_token="test-token")))
    return TestClient(
        app, client=("127.0.0.1", 50000),
        headers={"Authorization": "Bearer test-token"},
    ), service


class AgentRoutesTest(unittest.TestCase):
    def test_default_service_fallback_builds_router(self) -> None:
        # Covers the defensive branch when agent_service is None (line 78-79).
        router = build_agent_router()
        self.assertIsNotNone(router)

    def test_execute_returns_json_result(self) -> None:
        client, service = _build_client()
        response = client.post(
            "/v1/agent/execute",
            json={"task": "summarize the repo"},
            headers={"accept": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        (name, (request, owner, event_sink, _)) = service.calls[0]
        self.assertEqual(name, "execute")
        self.assertIsNone(event_sink)
        self.assertEqual(owner, DEFAULT_MEMORY_OWNER)
        self.assertEqual(request.task, "summarize the repo")

    def test_execute_streams_sse_events(self) -> None:
        client, service = _build_client()
        response = client.post(
            "/v1/agent/execute",
            json={"task": "do something"},
            headers={"accept": "text/event-stream"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        body = response.text
        self.assertIn("event: status", body)
        self.assertIn("event: step", body)
        self.assertIn("event: done", body)
        self.assertIn('"status": "ok"', body)
        (name, (_request, owner, event_sink, _host)) = service.calls[0]
        self.assertEqual(name, "execute")
        self.assertEqual(owner, DEFAULT_MEMORY_OWNER)
        self.assertIsNotNone(event_sink)

    def test_execute_streams_error_event_when_service_fails(self) -> None:
        failing = _FakeAgentService(execute_error=RuntimeError("boom"))
        client, _ = _build_client(failing)
        response = client.post(
            "/v1/agent/execute",
            json={"task": "do something"},
            headers={"accept": "text/event-stream"},
        )
        body = response.text
        self.assertIn("event: error", body)
        self.assertIn("boom", body)

    def test_execute_streams_error_event_when_worker_raises_base_exception(self) -> None:
        class _WorkerAbort(BaseException):
            pass

        failing = _FakeAgentService(execute_error=_WorkerAbort())  # type: ignore[arg-type]
        client, _ = _build_client(failing)
        responses: list[str] = []
        errors: list[BaseException] = []

        def post_stream() -> None:
            try:
                resp = client.post(
                    "/v1/agent/execute",
                    json={"task": "do something"},
                    headers={"accept": "text/event-stream"},
                )
                responses.append(resp.text)
            except BaseException as exc:
                errors.append(exc)

        reader = threading.Thread(target=post_stream, daemon=True)
        reader.start()
        reader.join(timeout=5)
        self.assertFalse(reader.is_alive(), "SSE stream hung when worker raised BaseException")
        self.assertEqual(errors, [])
        self.assertIn("event: error", responses[0])
        self.assertIn("_WorkerAbort", responses[0])

    def test_cancel_route(self) -> None:
        client, service = _build_client()
        response = client.post("/v1/agent/cancel", json={"session_id": "s1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertEqual(service.calls[0][0], "cancel")

    def test_ui_action_result_route(self) -> None:
        client, service = _build_client()
        response = client.post(
            "/v1/agent/ui-action-result",
            json={"request_id": "r1", "success": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.calls[0][0], "ui_action_result")

    def test_approve_route(self) -> None:
        client, service = _build_client()
        response = client.post(
            "/v1/agent/approve",
            json={"action_id": "a1", "approved": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approved")
        self.assertEqual(service.calls[0][0], "approve")

    def test_disconnect_cancels_the_running_agent(self) -> None:
        cancelled = []
        events: queue.Queue = queue.Queue()
        iterator = iter(_sse_events(events, on_disconnect=lambda: cancelled.append(True)))
        self.assertIn("event: status", next(iterator))
        iterator.close()  # what Starlette does when the client drops
        self.assertEqual(cancelled, [True])

    def test_a_completed_stream_is_not_cancelled(self) -> None:
        cancelled = []
        events: queue.Queue = queue.Queue()
        events.put(("result", {"status": "completed"}))
        list(_sse_events(events, on_disconnect=lambda: cancelled.append(True)))
        self.assertEqual(cancelled, [])

    def test_the_disconnect_callback_targets_the_owning_session(self) -> None:
        service = _FakeAgentService()
        _cancel_agent_run(service, "s1")
        self.assertEqual(service.calls[0][0], "cancel")
        self.assertEqual(service.calls[0][1].session_id, "s1")

    def test_a_disconnect_after_the_run_finished_is_ignored(self) -> None:
        class _Gone(_FakeAgentService):
            def cancel(self, request):
                raise RuntimeError

        _cancel_agent_run(_Gone(), "s1")

    def test_request_validation_rejects_bad_language(self) -> None:
        client, _ = _build_client()
        response = client.post(
            "/v1/agent/execute",
            json={"task": "x", "language": "fr"},
            headers={"accept": "application/json"},
        )
        self.assertEqual(response.status_code, 422)


class AgentStreamWorkerTrackerTests(unittest.TestCase):
    def test_start_failure_unregisters_the_worker(self) -> None:
        tracker = AgentStreamWorkerTracker()
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("cannot start")):
            with self.assertRaisesRegex(RuntimeError, "cannot start"):
                tracker.start(lambda: None, lambda: None)
        self.assertEqual(tracker._workers, {})

    def test_close_cancels_and_drains_a_live_worker(self) -> None:
        tracker = AgentStreamWorkerTracker()
        release = threading.Event()
        started = threading.Event()

        def worker() -> None:
            started.set()
            release.wait(2)

        self.assertTrue(tracker.start(worker, release.set))
        self.assertTrue(started.wait(1))
        self.assertTrue(tracker.close(timeout_seconds=1))
        self.assertFalse(tracker.start(lambda: None, lambda: None))

    def test_close_reports_a_worker_that_ignores_cancellation(self) -> None:
        tracker = AgentStreamWorkerTracker()
        release = threading.Event()
        started = threading.Event()

        def worker() -> None:
            started.set()
            release.wait(2)

        self.assertTrue(tracker.start(worker, lambda: None))
        self.assertTrue(started.wait(1))
        try:
            self.assertFalse(tracker.close(timeout_seconds=0))
        finally:
            release.set()
            deadline = time.monotonic() + 1
            while tracker._workers and time.monotonic() < deadline:
                time.sleep(0.01)

    def test_close_contains_a_racing_cancel_callback(self) -> None:
        tracker = AgentStreamWorkerTracker()
        release = threading.Event()

        def cancel() -> None:
            release.set()
            raise RuntimeError("already finished")  # noqa: TRY003 - deliberate test sentinel

        self.assertTrue(tracker.start(lambda: release.wait(1), cancel))
        self.assertTrue(tracker.close(timeout_seconds=1))

    def test_stream_refuses_work_after_tracker_shutdown(self) -> None:
        tracker = AgentStreamWorkerTracker()
        self.assertTrue(tracker.close())
        response = _stream_langgraph_execution(
            lambda _sink: {"status": "ok"},
            worker_tracker=tracker,
        )
        self.assertEqual(response.media_type, "text/event-stream")


if __name__ == "__main__":
    unittest.main()
