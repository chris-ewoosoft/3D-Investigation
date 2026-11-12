from __future__ import annotations

import contextvars
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import uuid4

from ..domain.approvals import approval_fingerprint
from ..domain.security import Principal, classification_at_least
from ..domain.tools import ToolRequest, ToolResult, tool_result_succeeded
from ..ports import ToolCatalog, ToolCatalogEntry, ToolEventSink, ToolExecutor
from .approvals import InMemoryApprovalGrantStore
from .validation import validate_input


@dataclass(frozen=True, slots=True)
class _ExecutionOutcome:
    payload: object = None
    error: BaseException | None = None
    timed_out: bool = False
    refused: bool = False
    saturated: bool = False


class ToolExecutionService:
    """The only application path allowed to execute registered tools.

    Approval-gated tools are unlocked exclusively by a single-use grant
    :meth:`issue_approval_grant` created for the exact ``(tool, params)``
    pair a user approved — never by a boolean the caller can assert.

    The catalogue is the :class:`~ai_assistant.ports.ToolCatalog` *port*
    (ADR 0032), not the concrete plugin registry: the policy chokepoint
    depends on the contract, so the catalogue implementation stays
    replaceable.
    """

    def __init__(self, registry: ToolCatalog, event_sink: ToolEventSink | None = None,
                 grants: InMemoryApprovalGrantStore | None = None,
                 *, max_concurrent_workers: int = 32) -> None:
        self._registry = registry
        self._event_sink = event_sink
        self._grants = grants if grants is not None else InMemoryApprovalGrantStore()
        self._worker_lock = threading.Lock()
        self._workers: set[threading.Thread] = set()
        self._closing = False
        self._max_concurrent_workers = max(1, max_concurrent_workers)

    def _is_closing(self) -> bool:
        with self._worker_lock:
            return self._closing

    def execute(self, tool_name: str, parameters: Mapping[str, Any], principal: Principal,
                correlation_id: str | None = None, *, approval_token: str = "") -> ToolResult:
        request = ToolRequest(tool_name, dict(parameters), correlation_id or str(uuid4()))
        if self._is_closing():
            return self._finish(request, ToolResult(
                False,
                error_code="tool_service_closing",
                message="Tool execution service is shutting down",
            ))
        admitted = self._admit(request, principal, approval_token)
        if isinstance(admitted, ToolResult):
            return self._finish(request, admitted)
        outcome = self._run_bounded(
            admitted.executor,
            dict(request.parameters),
            admitted.spec.timeout_seconds,
        )
        if outcome.refused:
            return self._finish(request, ToolResult(
                False,
                error_code="tool_service_closing",
                message="Tool execution service is shutting down",
            ))
        if outcome.saturated:
            return self._finish(request, ToolResult(
                False,
                error_code="tool_workers_exhausted",
                message="Tool execution worker pool is saturated",
            ))
        if outcome.timed_out:
            return self._finish(request, ToolResult(
                False,
                {"outcome_unknown": True, "timeout_seconds": admitted.spec.timeout_seconds},
                "tool_timeout",
                f"Tool exceeded its {admitted.spec.timeout_seconds}s execution deadline; outcome is unknown",
            ))
        if outcome.error is not None:
            # The worker boundary contains plugin failures, including
            # BaseException subclasses that would otherwise terminate a
            # thread without completing the caller's result.
            return self._finish(request, ToolResult(
                False,
                error_code="tool_failure",
                message=str(outcome.error),
            ))
        payload = outcome.payload
        if not isinstance(payload, dict):
            return self._finish(request, ToolResult(False, error_code="invalid_tool_result", message="Tool returned a non-object"))
        # One rule for "did it work", and it is the domain's: absence of an
        # ``error`` is not enough, a command can finish with a non-zero exit
        # code, and a mutation can report ``success: false``. The application
        # used to re-spell half of that rule and dropped ``return_code``
        # entirely, so a failed command was reported as a success.
        success = tool_result_succeeded(payload)
        return self._finish(request, ToolResult(success, payload, None if success else "tool_failure"))

    def _run_bounded(self, executor: ToolExecutor, parameters: dict[str, Any],
                     timeout_seconds: int) -> _ExecutionOutcome:
        """Run one executor behind the declared wall-clock deadline.

        Python cannot safely kill an arbitrary in-process thread. A timeout
        therefore bounds the caller and reports ``outcome_unknown`` while the
        tracker continues to own the worker. Shutdown closes admission and
        drains those workers before shared stores are released, so a timed-out
        call can never become detached code racing destroyed dependencies.
        """
        done = threading.Event()
        holder: dict[str, object] = {}
        execution_context = contextvars.copy_context()

        def invoke() -> None:
            try:
                holder["payload"] = executor(parameters)
            except BaseException as error:  # Thread boundary must always complete.
                holder["error"] = error
            finally:
                with self._worker_lock:
                    self._workers.discard(threading.current_thread())
                done.set()

        worker = threading.Thread(
            target=lambda: execution_context.run(invoke),
            daemon=True,
            name="tool-execution",
        )
        with self._worker_lock:
            if self._closing:
                return _ExecutionOutcome(refused=True)
            if len(self._workers) >= self._max_concurrent_workers:
                return _ExecutionOutcome(saturated=True)
            self._workers.add(worker)
        try:
            worker.start()
        except BaseException as error:
            with self._worker_lock:
                self._workers.discard(worker)
            return _ExecutionOutcome(error=error)
        if not done.wait(timeout_seconds):
            return _ExecutionOutcome(timed_out=True)
        holder_error = holder.get("error")
        return _ExecutionOutcome(
            payload=holder.get("payload"),
            error=holder_error if isinstance(holder_error, BaseException) else None,
        )

    def close(self, timeout_seconds: float = 5.0) -> bool:
        """Refuse new executions and boundedly drain every active worker."""
        with self._worker_lock:
            self._closing = True
            workers = tuple(self._workers)
        deadline = monotonic() + max(0.0, timeout_seconds)
        for worker in workers:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            worker.join(remaining)
        with self._worker_lock:
            return not self._workers

    def preflight(self, tool_name: str, parameters: Mapping[str, Any], principal: Principal,
                  correlation_id: str | None = None, *, approval_token: str = "") -> ToolResult:
        """Clear the policy gate for a dispatch that executes somewhere else.

        The remote A2A leg leaves this process, so it must pass the same
        validation, scope, classification and single-use grant checks the local
        path passes — and spend that grant — without the executor running here.
        A refusal is the caller's instruction not to dispatch.
        """
        request = ToolRequest(tool_name, dict(parameters), correlation_id or str(uuid4()))
        admitted = self._admit(request, principal, approval_token)
        if isinstance(admitted, ToolResult):
            return self._finish(request, admitted)
        return self._finish(request, ToolResult(True, {"tool": tool_name, "preflight": True}))

    def _admit(self, request: ToolRequest, principal: Principal,
               approval_token: str) -> ToolCatalogEntry | ToolResult:
        """Validate and authorise one request: the refusal, or the tool to run."""
        item = self._registry.tool(request.tool_name)
        if item is None:
            return ToolResult(False, error_code="tool_not_found", message=f"Unknown tool: {request.tool_name}")
        spec = item.spec
        validation_error = validate_input(spec.input_schema, request.parameters)
        if validation_error:
            return ToolResult(False, error_code="invalid_input", message=validation_error)
        if not principal.permits(spec.required_scope):
            return ToolResult(False, error_code="forbidden", message="Required tool scope is missing")
        # The principal's clearance must cover the tool's ceiling: a caller
        # that may only work with internal data may not drive a tool that
        # touches restricted data. The comparison used to run the other way
        # round (``tool >= principal``), which denied the *most* trusted
        # callers and let the least trusted ones through — and every tool
        # carried the same ceiling, so the gate was a constant comparison.
        if not classification_at_least(principal.classification, spec.maximum_classification):
            return ToolResult(False, error_code="data_policy_denied", message="Data classification is not permitted")
        if spec.requires_approval and not self._grants.covers(
                approval_token, approval_fingerprint(spec.name, request.parameters)):
            return ToolResult(False, {"approval_required": True, "tool": spec.name},
                              "approval_required", "User approval is required")
        return item

    def issue_approval_grant(self, tool_name: str, parameters: Mapping[str, Any], token: str) -> None:
        """Bind a single-use grant to the invocation the user just approved.

        Only a path that represents a user decision may call this.
        """
        self._grants.issue(token, approval_fingerprint(tool_name, parameters))

    def approval_covers(self, tool_name: str, parameters: Mapping[str, Any], token: str) -> bool:
        """Whether ``token`` still unlocks this invocation (does not spend it)."""
        return self._grants.peek(token, approval_fingerprint(tool_name, parameters))

    def _finish(self, request: ToolRequest, result: ToolResult) -> ToolResult:
        if self._event_sink:
            self._event_sink(request, result)
        return result
