"""Guard-path coverage for the tool execution gateway.

``ToolExecutionService`` is the only application path allowed to run a
registered tool, so every rejection it can produce is a security boundary
worth pinning: unknown tool, schema violation, missing scope, denied data
classification, missing approval grant, a crashing executor and a lying
executor (non-object payload). The single-use grant lifecycle and the
observability sink complete the contract.
"""
from __future__ import annotations

import contextvars
import threading
import unittest
from unittest.mock import patch

from ai_assistant.application.tools import ToolExecutionService
from ai_assistant.domain.security import (
    DataClassification,
    Principal,
    clearance_for_scopes,
    principal_for,
)
from ai_assistant.domain.tools import ToolSpec
from ai_assistant.plugins.registry import PluginRegistry

_SCHEMA = {
    "type": "object",
    "required": ["path"],
    "properties": {"path": {"type": "string"}},
}


def _spec(**overrides) -> ToolSpec:
    fields = {
        "name": "read_file",
        "description": "read a file",
        "input_schema": _SCHEMA,
        "timeout_seconds": 30,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def _service(executor=None, spec: ToolSpec | None = None, sink=None) -> ToolExecutionService:
    registry = PluginRegistry(allowed_plugins=frozenset({"builtin"}))
    registry.register_tool(spec or _spec(), executor or (lambda params: {"content": "x"}))
    return ToolExecutionService(registry, event_sink=sink)


def _principal(**overrides) -> Principal:
    args = {
        "subject": "tester",
        "scopes": frozenset({"read"}),
        # A principal with the full clearance: the refusal paths below are
        # about *one* gate at a time, so the clearance must not be the gate
        # that fires by accident.
        "classification": DataClassification.REGULATED,
    }
    args.update(overrides)
    return Principal(*args.values())


class UnknownToolTests(unittest.TestCase):
    def test_an_unregistered_tool_is_rejected_before_anything_else(self):
        result = _service().execute("no_such_tool", {}, _principal(), correlation_id="c1")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "tool_not_found")
        self.assertIn("no_such_tool", result.message)


class InputValidationTests(unittest.TestCase):
    def test_a_missing_required_parameter_is_rejected(self):
        result = _service().execute("read_file", {}, _principal(), correlation_id="c1")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "invalid_input")
        self.assertIn("path", result.message)


class ScopeEnforcementTests(unittest.TestCase):
    def test_a_principal_without_the_scope_is_forbidden(self):
        service = _service(spec=_spec(required_scope="admin:write"))
        result = service.execute("read_file", {"path": "a"}, _principal(), correlation_id="c1")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "forbidden")

    def test_a_principal_holding_the_scope_passes(self):
        service = _service(spec=_spec(required_scope="read"))
        result = service.execute("read_file", {"path": "a"}, _principal(), correlation_id="c1")
        self.assertTrue(result.success)


class ClearanceTests(unittest.TestCase):
    """The clearance a principal carries is derived from the granted scopes.

    A caller that labels its own payload ``regulated`` must not thereby buy
    the right to drive a tool that touches restricted data: the clearance
    follows the deployment's scope map, not the caller's claim.
    """

    def test_a_read_only_principal_has_internal_clearance(self):
        self.assertEqual(
            clearance_for_scopes(frozenset({"project.read"})),
            DataClassification.INTERNAL,
        )

    def test_an_elevated_scope_raises_the_clearance(self):
        for scope in ("project.write", "project.execute", "desktop.action"):
            with self.subTest(scope=scope):
                self.assertEqual(
                    clearance_for_scopes(frozenset({scope})),
                    DataClassification.REGULATED,
                )

    def test_a_principal_carries_the_clearance_of_its_scopes(self):
        self.assertEqual(
            principal_for("t", {"project.read"}).classification,
            DataClassification.INTERNAL,
        )
        self.assertEqual(
            principal_for("t", {"project.execute"}).classification,
            DataClassification.REGULATED,
        )

    def test_a_read_only_principal_cannot_drive_a_restricted_tool(self):
        """The two gates are different axes: scope says *what*, clearance
        says *how sensitive*. A principal that holds the write scope but not
        the clearance is still refused."""
        service = _service(spec=_spec(
            required_scope="project.write",
            maximum_classification=DataClassification.RESTRICTED,
        ))
        principal = Principal("reader", frozenset({"project.write"}), DataClassification.INTERNAL)
        result = service.execute("read_file", {"path": "a"}, principal, correlation_id="c1")
        self.assertEqual(result.error_code, "data_policy_denied")


    def test_a_failed_command_is_a_failure(self):
        """A non-zero exit code is a failure, not a success.

        The application re-spelled half of the domain's success rule and
        dropped ``return_code`` entirely, so a command that failed was
        reported as a success — to the event sink, to the audit log and to
        the model.
        """
        service = _service(executor=lambda params: {"return_code": 1, "stderr": "boom"})
        result = service.execute("read_file", {"path": "a"}, _principal(), correlation_id="c1")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "tool_failure")


class DataClassificationTests(unittest.TestCase):
    """The clearance gate: a principal may not drive a tool whose data
    ceiling is above its own clearance.

    The comparison used to run the other way round (``tool >= principal``),
    which denied the *most* trusted callers and let the least trusted ones
    through — and every tool carried the same ceiling, so the gate was a
    comparison against a constant. These cases pin the intended direction.
    """

    def test_a_principal_without_the_clearance_cannot_use_a_restricted_tool(self):
        service = _service()  # default ceiling is RESTRICTED
        principal = _principal(classification=DataClassification.INTERNAL)
        result = service.execute("read_file", {"path": "a"}, principal, correlation_id="c1")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "data_policy_denied")

    def test_a_principal_with_the_clearance_may_use_a_restricted_tool(self):
        service = _service()
        principal = _principal(classification=DataClassification.REGULATED)
        result = service.execute("read_file", {"path": "a"}, principal, correlation_id="c1")
        self.assertTrue(result.success)

    def test_a_read_only_tool_is_within_an_internal_principal_s_clearance(self):
        """The ceiling is the tool's own, not a constant for every tool."""
        service = _service(spec=_spec(maximum_classification=DataClassification.INTERNAL))
        principal = _principal(classification=DataClassification.INTERNAL)
        result = service.execute("read_file", {"path": "a"}, principal, correlation_id="c1")
        self.assertTrue(result.success)


class ApprovalGrantTests(unittest.TestCase):
    def test_a_gated_tool_without_a_grant_asks_for_approval(self):
        service = _service(spec=_spec(requires_approval=True))
        result = service.execute("read_file", {"path": "a"}, _principal(), correlation_id="c1")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "approval_required")
        self.assertTrue(result.payload["approval_required"])
        self.assertEqual(result.payload["tool"], "read_file")

    def test_an_issued_grant_unlocks_exactly_one_invocation(self):
        service = _service(spec=_spec(requires_approval=True))
        params = {"path": "a"}
        service.issue_approval_grant("read_file", params, token="tok-1")
        first = service.execute("read_file", params, _principal(), approval_token="tok-1")
        self.assertTrue(first.success)
        # Single use: the same token is spent.
        second = service.execute("read_file", params, _principal(), approval_token="tok-1")
        self.assertEqual(second.error_code, "approval_required")

    def test_approval_covers_peeks_without_spending(self):
        """``approval_covers`` answers without consuming the grant."""
        service = _service(spec=_spec(requires_approval=True))
        service.issue_approval_grant("read_file", {"path": "a"}, token="tok-2")
        self.assertTrue(service.approval_covers("read_file", {"path": "a"}, "tok-2"))
        self.assertFalse(service.approval_covers("read_file", {"path": "b"}, "tok-2"))
        # Still unspent, so execution succeeds.
        result = service.execute("read_file", {"path": "a"}, _principal(), approval_token="tok-2")
        self.assertTrue(result.success)


class PreflightTests(unittest.TestCase):
    """A dispatch that runs elsewhere clears the very same gate, then leaves.

    The remote A2A leg never reaches the local executor, so ``preflight`` is
    what keeps validation, scope, classification and the single-use grant from
    being skipped by leaving the process.
    """

    def test_an_admitted_call_never_runs_the_executor(self):
        runs: list[dict] = []
        service = _service(executor=lambda params: runs.append(params) or {"content": "x"})
        result = service.preflight("read_file", {"path": "a"}, _principal(), correlation_id="c1")

        self.assertTrue(result.success)
        self.assertTrue(result.payload["preflight"])
        self.assertEqual(result.payload["tool"], "read_file")
        self.assertEqual(runs, [])

    def test_every_refusal_is_returned_instead_of_dispatching(self):
        cases = [
            ("tool_not_found", _service(), "no_such_tool", {}, _principal()),
            ("invalid_input", _service(), "read_file", {}, _principal()),
            ("forbidden", _service(spec=_spec(required_scope="admin:write")),
             "read_file", {"path": "a"}, _principal()),
            ("data_policy_denied", _service(), "read_file", {"path": "a"},
             _principal(classification=DataClassification.INTERNAL)),
            ("approval_required", _service(spec=_spec(requires_approval=True)),
             "read_file", {"path": "a"}, _principal()),
        ]
        for expected, service, tool, params, principal in cases:
            with self.subTest(expected=expected):
                result = service.preflight(tool, params, principal, correlation_id="c1")
                self.assertFalse(result.success)
                self.assertEqual(result.error_code, expected)

    def test_preflight_spends_the_single_use_grant(self):
        service = _service(spec=_spec(requires_approval=True))
        params = {"path": "a"}
        service.issue_approval_grant("read_file", params, token="tok-9")

        authorised = service.preflight("read_file", params, _principal(), approval_token="tok-9")
        self.assertTrue(authorised.success)
        # Authorising the remote dispatch spent it: the probe says so...
        self.assertFalse(service.approval_covers("read_file", params, "tok-9"))
        # ...and a replay through the local path is refused.
        replay = service.execute("read_file", params, _principal(), approval_token="tok-9")
        self.assertEqual(replay.error_code, "approval_required")

    def test_preflight_is_reported_to_the_audit_sink(self):
        events: list[tuple] = []
        service = _service(sink=lambda request, result: events.append((request, result)))
        service.preflight("read_file", {"path": "a"}, _principal(), correlation_id="c-remote")
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0][1].success)


class ExecutorContractTests(unittest.TestCase):
    def test_a_crashing_executor_becomes_a_structured_failure(self):
        def _boom(params):
            msg = "disk gone"
            raise RuntimeError(msg)

        result = _service(executor=_boom).execute(
            "read_file", {"path": "a"}, _principal(), correlation_id="c1",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "tool_failure")
        self.assertIn("disk gone", result.message)

    def test_a_non_object_payload_is_rejected(self):
        result = _service(executor=lambda params: "oops").execute(
            "read_file", {"path": "a"}, _principal(), correlation_id="c1",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "invalid_tool_result")

    def test_an_error_payload_is_a_tool_failure(self):
        result = _service(executor=lambda params: {"error": "boom"}).execute(
            "read_file", {"path": "a"}, _principal(), correlation_id="c1",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "tool_failure")

    def test_a_clean_payload_succeeds(self):
        result = _service().execute("read_file", {"path": "a"}, _principal(), correlation_id="c1")
        self.assertTrue(result.success)
        self.assertIsNone(result.error_code)

    def test_a_base_exception_is_contained_by_the_worker_boundary(self):
        def stop_thread(_params):
            raise SystemExit("plugin exited")  # noqa: TRY003 - deliberate boundary sentinel

        result = _service(executor=stop_thread).execute(
            "read_file", {"path": "a"}, _principal(), correlation_id="c1",
        )
        self.assertEqual(result.error_code, "tool_failure")
        self.assertIn("plugin exited", result.message)

    def test_execution_context_reaches_the_worker(self):
        marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="missing")
        marker.set("trace-value")
        result = _service(executor=lambda _params: {"context": marker.get()}).execute(
            "read_file", {"path": "a"}, _principal(), correlation_id="c1",
        )
        self.assertEqual(result.payload["context"], "trace-value")

    def test_thread_start_failure_is_structured_and_unregistered(self):
        service = _service()
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("start failed")):
            result = service.execute(
                "read_file", {"path": "a"}, _principal(), correlation_id="c1",
            )
        self.assertEqual(result.error_code, "tool_failure")
        self.assertIn("start failed", result.message)
        self.assertTrue(service.close())


class ExecutorDeadlineTests(unittest.TestCase):
    def test_timeout_bounds_the_caller_and_keeps_the_unknown_worker_owned(self):
        release = threading.Event()
        started = threading.Event()

        def slow(_params):
            started.set()
            release.wait(5)
            return {"content": "late"}

        service = _service(executor=slow, spec=_spec(timeout_seconds=1))
        result = service.execute(
            "read_file", {"path": "a"}, _principal(), correlation_id="deadline",
        )
        self.assertTrue(started.is_set())
        self.assertEqual(result.error_code, "tool_timeout")
        self.assertTrue(result.payload["outcome_unknown"])
        self.assertEqual(result.payload["timeout_seconds"], 1)
        self.assertFalse(service.close(timeout_seconds=0))
        release.set()
        self.assertTrue(service.close(timeout_seconds=1))

    def test_close_refuses_new_execution_without_running_the_executor(self):
        runs: list[dict] = []
        service = _service(executor=lambda params: runs.append(params) or {"content": "x"})
        self.assertTrue(service.close())
        result = service.execute(
            "read_file", {"path": "a"}, _principal(), correlation_id="closed",
        )
        self.assertEqual(result.error_code, "tool_service_closing")
        self.assertEqual(runs, [])

    def test_close_preserves_single_use_approval_grant_and_handles_race(self):
        service = _service(spec=_spec(requires_approval=True))
        params = {"path": "a"}
        service.issue_approval_grant("read_file", params, token="tok-close")
        self.assertTrue(service.close())

        refused = service.execute(
            "read_file", params, _principal(), correlation_id="c-closed", approval_token="tok-close",
        )
        self.assertEqual(refused.error_code, "tool_service_closing")
        self.assertTrue(service.approval_covers("read_file", params, "tok-close"))

        # Cover concurrent close arriving between _admit and _run_bounded.
        raced_service = _service()
        original_admit = raced_service._admit

        def close_during_admit(req, principal, token):
            res = original_admit(req, principal, token)
            raced_service.close()
            return res

        with patch.object(raced_service, "_admit", side_effect=close_during_admit):
            raced = raced_service.execute("read_file", {"path": "a"}, _principal(), correlation_id="c-race")
        self.assertEqual(raced.error_code, "tool_service_closing")

    def test_saturated_worker_pool_refuses_without_spawning(self):
        release = threading.Event()
        started = threading.Event()
        runs = 0

        def slow(_params):
            nonlocal runs
            runs += 1
            started.set()
            release.wait(5)
            return {"content": "ok"}

        registry = PluginRegistry(allowed_plugins=frozenset({"builtin"}))
        registry.register_tool(_spec(timeout_seconds=1), slow)
        service = ToolExecutionService(registry, max_concurrent_workers=1)
        try:
            first = service.execute("read_file", {"path": "a"}, _principal(), correlation_id="t1")
            self.assertEqual(first.error_code, "tool_timeout")
            self.assertTrue(started.is_set())
            second = service.execute("read_file", {"path": "a"}, _principal(), correlation_id="t2")
            self.assertEqual(second.error_code, "tool_workers_exhausted")
            self.assertEqual(runs, 1)
        finally:
            release.set()
            self.assertTrue(service.close(timeout_seconds=2))


class ToolSpecDeadlineTests(unittest.TestCase):
    def test_timeout_must_be_a_positive_integer(self):
        for invalid in (0, -1, True, 1.5, "10"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer timeout"):
                    _spec(timeout_seconds=invalid)


class EventSinkTests(unittest.TestCase):
    def test_every_outcome_is_reported_to_the_sink(self):
        events: list[tuple] = []
        service = _service(sink=lambda request, result: events.append((request, result)))
        service.execute("read_file", {"path": "a"}, _principal(), correlation_id="c9")
        service.execute("no_such_tool", {}, _principal(), correlation_id="c10")
        self.assertEqual(len(events), 2)
        request, result = events[0]
        self.assertEqual(request.tool_name, "read_file")
        self.assertEqual(request.correlation_id, "c9")
        self.assertTrue(result.success)
        self.assertFalse(events[1][1].success)


if __name__ == "__main__":
    unittest.main()
