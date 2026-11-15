# ADR 0060: Bounded LSP I/O and durable worker handoff

Date: 2026-11-16

## Status

Accepted.

## Context

An independent source review found two runtime ownership gaps that the green
coverage and mutation tables did not expose.

The LSP client checked a deadline around synchronous ``readline``/``read``
calls. A live language server that emitted no bytes could therefore block the
reader forever. The same reader accepted the first JSON-RPC message instead of
the response with the requested id, so a notification could replace a real
result. Its kill fallback also did not perform the final reap.

``TaskService`` committed ``SUBMITTED`` or ``WORKING`` before starting the
owning thread. If ``Thread.start()`` failed, or shutdown won between the first
admission check and the commit/start handoff, durable state could remain
non-terminal without a worker in the current process.

## Decision

- One LSP response-reader thread owns every blocking pipe read. Callers wait on
  a queue with a real wall-clock deadline and accept only the requested
  JSON-RPC id; notifications and unrelated responses are ignored.
- LSP teardown always attempts ``terminate``, bounded ``wait``, ``kill`` when
  needed, a second bounded ``wait``, independent pipe closure and reader join.
- ``TaskService`` holds its lifecycle lock across the durable commit and worker
  registration/start boundary and re-checks shutdown while holding that lock.
- A failed initial start is durably compensated to ``FAILED``. A failed resume
  is restored to ``INPUT_REQUIRED`` with the consumed resume payload removed,
  so the caller can retry. Both transitions are compare-and-set writes and do
  not overwrite a concurrent newer state.

## Verification

The LSP suite uses a real silent child process to prove the deadline, injects a
notification before the requested response, and verifies terminate/kill/reap
ownership. The task suite verifies both shutdown races, both start-failure
compensations, and the initial compensation against a real ``SqliteTaskStore``.

Seven decision mutants pin the complete class: timeout, response correlation,
kill reaping, both lifecycle-lock re-checks and both durable compensations.

## Consequences

The LSP caller is bounded even when the child remains alive and silent, while
the reader remains owned until process teardown. Durable task state can no
longer advertise runnable work for a thread that was never started. The extra
reader thread exists only for the lifetime of one LSP subprocess and is joined
during teardown.
