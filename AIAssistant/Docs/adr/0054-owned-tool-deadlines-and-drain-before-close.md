# ADR 0054: Owned tool deadlines and drain-before-close

## Context

Every `ToolSpec` declared `timeout_seconds`, but the only execution chokepoint
called the executor directly. A blocked plugin, retrieval call or filesystem
operation could therefore block the agent indefinitely. The parallel tool
batch also waited for that call forever. The number advertised policy that no
runtime component enforced.

Shutdown had the same ownership gap beyond HTTP streaming. `TaskService`
owned A2A worker threads but its `close()` did not report whether they drained,
and the lifespan closed checkpoint/memory dependencies before closing the task
owner. A late task or tool could consequently run against released stores.

## Decision

`ToolExecutionService` is the deadline owner as well as the policy gateway.
After admission it registers a daemon worker, propagates the caller's Python
context into it, and waits no longer than `ToolSpec.timeout_seconds`.
`ToolSpec` refuses zero, negative, boolean and non-integer deadlines at
construction.

A Python process cannot safely kill an arbitrary in-process thread. On a
deadline the gateway therefore returns a structured `tool_timeout` with
`outcome_unknown=true`; it does not lie that a write was rolled back or invite
an unsafe automatic retry. The orchestration recorder treats that flag as a
terminal state for both sequential and parallel execution: it preserves the
tool result for audit, reports that external state must be reconciled, and does
not enter verification/reflection/reasoning. The late worker remains
registered. New executions are refused once shutdown starts, and
`ToolExecutionService.close()` boundedly drains every registered worker.

The shutdown order now closes admission and drains all three worker owners:
HTTP SSE, A2A tasks and tool execution. `TaskService.close()` reports whether
its workers drained. Checkpoint and long-term-memory stores close only when all
owners report success. A failure or expired drain leaves shared resources open
for OS process teardown rather than destroying them under live code.

## Verification

`tests/unit/test_tool_gateway_guards.py` pins the deadline, unknown-outcome
shape, shutdown admission, thread-start failure, `BaseException` containment,
context propagation and positive-integer timeout contract.
`tests/unit/test_task_lifecycle_guards.py` pins the task drain result.
`tests/integration/test_server_module.py` proves that either a task or a tool
worker prevents shared-store close while all owners still receive their close
signal.

Six new mutants cover deadline waiting, tool admission, context propagation,
task drain truthfulness, timeout validation and the shared sequential/batch
no-retry decision. The existing lifecycle mutant is broadened from SSE-only to
the combined runtime drain. `MIN_MUTANTS` moves from 280 to 286.
