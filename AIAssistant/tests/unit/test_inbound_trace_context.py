"""Guard coverage for the inbound half of W3C trace propagation (ADR 0001).

``test_a2a_client`` pins the outbound half — the A2A client attaching
``traceparent`` on the way out. This file pins everything that must happen
for that header to mean something when it arrives:

* ``_parse_traceparent`` — version-00 grammar, all-zero ids rejected;
* ``attach_inbound_traceparent`` — the remote parent attach, plus every
  fail-open arm (absent header, malformed header, SDK not importable) that
  has to yield a no-op instead of failing the request;
* ``TraceContextMiddleware`` — the ASGI wiring: HTTP scopes hand the header
  over, every other scope passes through untouched;
* ``TaskService._start`` — the ``contextvars`` snapshot that carries the
  submitter's context into the worker thread, which a bare
  ``threading.Thread`` would otherwise drop;
* one HTTP test that drives the whole chain: header → middleware → sync
  route (threadpool) → ``submit`` → worker thread.

CI installs no ``opentelemetry`` package, so the attach arms are driven with
fake modules in ``sys.modules`` — the same technique as
``test_tracing_sandbox_guards``. The fake ``opentelemetry.context`` parks the
attached context in a real ``ContextVar`` (the storage the real SDK also
uses), so propagation is asserted as observed state rather than as call
bookkeeping.
"""
from __future__ import annotations

import asyncio
import contextvars
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_assistant.adapters.a2a_router import build_a2a_router
from ai_assistant.adapters.persistence import SqliteTaskStore
from ai_assistant.application.tasks import TaskService
from ai_assistant.bootstrap import app_factory
from ai_assistant.domain.tasks import TaskStatus
from ai_assistant.observability import tracing
from ai_assistant.settings import ArchitectureSettings

#: The canonical W3C example ids, so the expected values read as hex literals.
_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
_TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
_SPAN_ID = 0xB7AD6B7169203331


def _fake_otel_modules() -> tuple[dict[str, ModuleType], dict]:
    """Fake ``opentelemetry`` packages recording attach/detach traffic.

    ``context.attach`` parks the remote parent in a real ``ContextVar`` — the
    storage the real SDK uses — so callers can assert propagation as observed
    state, not as call bookkeeping. Returns the ``sys.modules`` replacement
    and the shared recorder.
    """
    recorded: dict = {}

    trace_mod = ModuleType("opentelemetry.trace")

    class _SpanContext:
        def __init__(self, trace_id: int, span_id: int, is_remote: bool = False,
                     trace_flags: int = 0) -> None:
            self.trace_id = trace_id
            self.span_id = span_id
            self.is_remote = is_remote
            self.trace_flags = trace_flags

    class _NonRecordingSpan:
        def __init__(self, span_context: _SpanContext) -> None:
            self._span_context = span_context

        def get_span_context(self) -> _SpanContext:
            return self._span_context

    def _trace_flags(flags: int) -> int:
        return flags

    def _set_span_in_context(span: _NonRecordingSpan) -> dict:
        return {"span": span}

    trace_mod.SpanContext = _SpanContext
    trace_mod.NonRecordingSpan = _NonRecordingSpan
    trace_mod.TraceFlags = _trace_flags
    trace_mod.set_span_in_context = _set_span_in_context

    marker: contextvars.ContextVar = contextvars.ContextVar(
        "fake_otel_context", default=None
    )
    recorded["marker"] = marker

    context_mod = ModuleType("opentelemetry.context")

    def _attach(context: dict) -> object:
        recorded["attached"] = context
        return marker.set(context)

    def _detach(token: object) -> None:
        recorded["detached"] = token
        marker.reset(token)  # type: ignore[arg-type]

    context_mod.attach = _attach
    context_mod.detach = _detach

    top = ModuleType("opentelemetry")
    top.context = context_mod
    top.trace = trace_mod
    modules = {
        "opentelemetry": top,
        "opentelemetry.context": context_mod,
        "opentelemetry.trace": trace_mod,
    }
    return modules, recorded


class TraceparentParserTests(unittest.TestCase):
    """The W3C grammar as received: version 00 only, no all-zero ids."""

    def test_a_valid_traceparent_yields_ids_and_flags(self) -> None:
        self.assertEqual(
            tracing._parse_traceparent(_TRACEPARENT), (_TRACE_ID, _SPAN_ID, 1)
        )

    def test_an_absent_or_empty_header_is_not_a_traceparent(self) -> None:
        self.assertIsNone(tracing._parse_traceparent(None))
        self.assertIsNone(tracing._parse_traceparent(""))

    def test_a_malformed_header_is_not_a_traceparent(self) -> None:
        for header in (
            "garbage",
            f"01-{_TRACEPARENT[3:]}",            # a future version, not ours
            "00-tooshort-b7ad6b7169203331-01",   # not 32 hex digits
            f"{_TRACEPARENT}-extra",             # a fifth field
            _TRACEPARENT[:-3],                   # flags missing
        ):
            with self.subTest(header=header):
                self.assertIsNone(tracing._parse_traceparent(header))

    def test_all_zero_ids_are_rejected_by_the_spec(self) -> None:
        self.assertIsNone(tracing._parse_traceparent(
            "00-00000000000000000000000000000000-b7ad6b7169203331-01"))
        self.assertIsNone(tracing._parse_traceparent(
            "00-0af7651916cd43dd8448eb211c80319c-0000000000000000-01"))

    def test_surrounding_whitespace_is_ignored(self) -> None:
        self.assertEqual(
            tracing._parse_traceparent(f"  {_TRACEPARENT}\n"),
            (_TRACE_ID, _SPAN_ID, 1),
        )


class InboundAttachTests(unittest.TestCase):
    """Attach the caller as a remote parent; unusable input is a no-op."""

    def test_a_valid_header_attaches_a_remote_parent_and_detaches(self) -> None:
        modules, recorded = _fake_otel_modules()
        with patch.dict(sys.modules, modules):
            with tracing.attach_inbound_traceparent(_TRACEPARENT):
                span_context = recorded["attached"]["span"].get_span_context()
                self.assertEqual(span_context.trace_id, _TRACE_ID)
                self.assertEqual(span_context.span_id, _SPAN_ID)
                self.assertTrue(span_context.is_remote)
                self.assertEqual(span_context.trace_flags, 1)
                self.assertNotIn("detached", recorded)
        self.assertIn("detached", recorded)

    def test_a_raising_block_still_detaches_the_parent(self) -> None:
        modules, recorded = _fake_otel_modules()
        with patch.dict(sys.modules, modules):
            with self.assertRaises(ValueError):
                with tracing.attach_inbound_traceparent(_TRACEPARENT):
                    raise ValueError("boom")
        self.assertIn("detached", recorded)

    def test_an_absent_header_never_reaches_the_sdk(self) -> None:
        modules, recorded = _fake_otel_modules()
        with patch.dict(sys.modules, modules):
            with tracing.attach_inbound_traceparent(None):
                pass
        self.assertNotIn("attached", recorded)

    def test_a_malformed_header_never_reaches_the_sdk(self) -> None:
        modules, recorded = _fake_otel_modules()
        with patch.dict(sys.modules, modules):
            with tracing.attach_inbound_traceparent("not-a-traceparent"):
                pass
        self.assertNotIn("attached", recorded)

    def test_an_unimportable_sdk_degrades_to_a_noop(self) -> None:
        """``None`` in ``sys.modules`` raises like an uninstalled package."""
        with patch.dict(sys.modules, {"opentelemetry": None}):
            with tracing.attach_inbound_traceparent(_TRACEPARENT):
                pass


class ScopeHeaderTests(unittest.TestCase):
    """Header extraction from an ASGI scope (case-insensitive, may be absent)."""

    def test_the_header_lookup_is_case_insensitive(self) -> None:
        scope = {"headers": [(b"x-request-id", b"1"),
                              (b"TraceParent", _TRACEPARENT.encode())]}
        self.assertEqual(app_factory._traceparent_from_scope(scope), _TRACEPARENT)

    def test_a_scope_without_headers_is_none(self) -> None:
        self.assertIsNone(app_factory._traceparent_from_scope({"type": "http"}))

    def test_headers_without_a_traceparent_are_none(self) -> None:
        self.assertIsNone(app_factory._traceparent_from_scope(
            {"type": "http", "headers": [(b"accept", b"*/*")]}))


class TraceContextMiddlewareTests(unittest.TestCase):
    """The ASGI wiring: HTTP scopes hand the header over, others pass through."""

    @staticmethod
    def _drive(scope: dict) -> tuple[list, str]:
        """Run one scope through the middleware; report what it handed over."""
        seen: list = []
        reached: list = []

        @contextmanager
        def _record(header: str | None):
            seen.append(header)
            yield

        async def _inner(scope: dict, receive: object, send: object) -> None:
            reached.append(scope["type"])

        async def _call() -> None:
            middleware = app_factory.TraceContextMiddleware(_inner)
            await middleware(scope, None, None)

        with patch.object(app_factory, "attach_inbound_traceparent", _record):
            asyncio.run(_call())
        return seen, reached[0]

    def test_an_http_scope_hands_over_the_traceparent(self) -> None:
        scope = {"type": "http",
                 "headers": [(b"x-request-id", b"1"),
                             (b"traceparent", _TRACEPARENT.encode())]}
        self.assertEqual(self._drive(scope), ([_TRACEPARENT], "http"))

    def test_an_http_scope_without_the_header_hands_over_none(self) -> None:
        scope = {"type": "http", "headers": [(b"content-type", b"application/json")]}
        self.assertEqual(self._drive(scope), ([None], "http"))

    def test_a_non_http_scope_bypasses_propagation(self) -> None:
        seen, reached = self._drive({"type": "lifespan"})
        self.assertEqual(seen, [])
        self.assertEqual(reached, "lifespan")


class WorkerContextSnapshotTests(unittest.TestCase):
    """``_start`` copies the submitter's contextvars into the worker thread."""

    def test_the_worker_inherits_the_context_but_stays_isolated(self) -> None:
        marker: contextvars.ContextVar[str] = contextvars.ContextVar(
            "marker", default="unset"
        )
        seen: list[str] = []

        def executor(task) -> dict:
            seen.append(marker.get())
            marker.set("changed-inside-worker")
            return {"answer": task.message}

        with tempfile.TemporaryDirectory() as directory:
            store = SqliteTaskStore(Path(directory) / "tasks.sqlite")
            service = TaskService(store, executor, frozenset({"supervisor"}))
            token = marker.set("from-request")
            try:
                task = service.submit("supervisor", "hello")
                deadline = time.monotonic() + 5
                status = None
                while time.monotonic() < deadline:
                    current = service.get(task.id)
                    status = current.status if current else None
                    if status == TaskStatus.COMPLETED:
                        break
                    time.sleep(0.01)
                self.assertEqual(status, TaskStatus.COMPLETED)
                # A bare thread starts with an empty context: only the snapshot
                # carries the submitter's value in …
                self.assertEqual(seen, ["from-request"])
                # … and the worker's own write does not come back out.
                self.assertEqual(marker.get(), "from-request")
            finally:
                marker.reset(token)
                service.close()


class InboundTraceparentOverHttpTests(unittest.TestCase):
    """The whole chain over real HTTP: header → middleware → worker thread.

    The A2A routes are sync (they run in the threadpool) and the task worker
    is a bare ``threading.Thread`` — neither inherits contextvars on its own.
    The fake OTel context lives in a real ``ContextVar`` (the real SDK's
    storage), so the executor observing the remote parent on the worker
    thread proves every hop carried it.
    """

    @staticmethod
    def _settings(directory: str) -> ArchitectureSettings:
        return ArchitectureSettings(
            profile="desktop",
            data_dir=Path(directory),
            enable_mcp=True,
            enable_a2a=True,
            allow_remote_a2a=False,
            trusted_a2a_endpoints=frozenset(),
            agent_capabilities=frozenset({"supervisor"}),
            capability_scopes={},
            allowed_plugins=frozenset(),
            allowed_origins=("*",),
        )

    def _run_request(self, headers: dict) -> object:
        """POST one A2A task through the real app; yield the executor's view."""
        modules, recorded = _fake_otel_modules()
        observed: list = []

        def executor(task) -> dict:
            observed.append(recorded["marker"].get())
            return {"answer": task.message}

        with tempfile.TemporaryDirectory() as directory:
            store = SqliteTaskStore(Path(directory) / "tasks.sqlite")
            service = TaskService(store, executor, frozenset({"supervisor"}))

            async def _lifespan(app) -> None:
                yield

            try:
                with patch.dict(sys.modules, modules):
                    app = app_factory.create_app(self._settings(directory), _lifespan)
                    app.include_router(build_a2a_router(service, "test-agent", "1.0.0"))
                    with TestClient(app, client=("127.0.0.1", 50000)) as client:
                        response = client.post("/a2a/tasks/send", json={
                            "capability": "supervisor",
                            "message": {"role": "user",
                                        "parts": [{"kind": "text", "text": "hello"}]},
                        }, headers=headers)
                        self.assertEqual(response.status_code, 200)
                        task_id = response.json()["task"]["id"]
                        deadline = time.monotonic() + 5
                        status = None
                        while time.monotonic() < deadline:
                            payload = client.get(f"/a2a/tasks/{task_id}").json()
                            status = payload["task"]["status"]["state"]
                            if status == TaskStatus.COMPLETED:
                                break
                            time.sleep(0.01)
                        self.assertEqual(status, TaskStatus.COMPLETED)
            finally:
                service.close()
        self.assertEqual(len(observed), 1)
        return observed[0]

    def test_the_remote_parent_reaches_the_worker_thread(self) -> None:
        context = self._run_request({"traceparent": _TRACEPARENT})
        self.assertIsNotNone(context)
        span_context = context["span"].get_span_context()
        self.assertEqual(span_context.trace_id, _TRACE_ID)
        self.assertEqual(span_context.span_id, _SPAN_ID)
        self.assertTrue(span_context.is_remote)

    def test_without_the_header_the_worker_sees_no_parent(self) -> None:
        self.assertIsNone(self._run_request({}))


if __name__ == "__main__":
    unittest.main()
