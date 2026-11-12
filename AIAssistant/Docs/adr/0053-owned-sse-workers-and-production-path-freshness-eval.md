# ADR 0053: Owned SSE workers and production-path freshness eval

## Context

`POST /v1/agent/execute` streamed an agent run from a detached daemon thread.
The FastAPI lifespan closed the checkpoint, memory and task stores without
knowing whether that thread was still using them. A disconnect attempted
cooperative cancellation, but process shutdown had no owner, admission gate
or bounded drain for the worker.

The model-in-loop release gate also measured only three planner kinds. The
runtime already had a fourth `web_search` kind and normal chat used a separate
model freshness decision, so the gate could remain green while either real
web-routing path regressed.

## Decision

The composition root owns one `AgentStreamWorkerTracker`. It registers every
SSE worker before start, refuses new workers once shutdown begins, invokes the
run's cooperative cancellation callback and joins all workers against one
bounded deadline. Persistence closes only after a successful drain. If a
worker ignores cancellation, the process deliberately leaves its dependencies
open for OS teardown instead of invalidating them under live code.

The chat freshness prompt and parsing now live in
`classify_web_freshness()`. Chat routing and the evaluator call that same
function. Unrecognised model output returns `None`: chat fails closed without
external egress and the evaluator records lost model coverage.

The planner golden set is a balanced 32-case matrix over Vietnamese/English
and all four kinds. A separate balanced 16-case matrix grades the exact chat
freshness classifier. CI gates freshness accuracy and coverage independently;
planner aggregate accuracy cannot hide a stale-answer regression.

## Verification

`tests/unit/test_http_agent_routes.py` pins admission, cancellation and bounded
drain. `tests/integration/test_server_module.py` proves that a non-drained
worker prevents dependency teardown. `tests/unit/test_web_search.py` pins the
closed classifier vocabulary, and `tests/unit/test_model_in_loop_eval.py` pins
both balanced matrices and the independent freshness gate.

Three new mutants cover shutdown admission, the no-close-under-live-worker
decision and the classifier's closed output vocabulary. `MIN_MUTANTS` moves
from 277 to 280.

