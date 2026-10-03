"""OpenTelemetry tracing and unified context managers."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .langsmith_integration import (
    _active_langsmith_run,
    finish_langsmith_run,
    start_langsmith_run,
)
from .metrics import _metrics

_enabled = os.getenv("AGENT_OBSERVABILITY", "0") == "1"
_tracer = None

if _enabled:
    try:
        from opentelemetry import trace
        _tracer = trace.get_tracer("3d-reconstruction.agent")
    except ImportError:
        pass


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Emit an OpenTelemetry span and a nested LangSmith run when enabled."""
    started = time.monotonic()
    trace_span = _tracer.start_as_current_span(name) if _tracer else None
    if trace_span:
        trace_span.__enter__()
        for key, value in attributes.items():
            if value is not None:
                trace_span.set_attribute(key, value)

    run_id = start_langsmith_run(name, "chain", {"attributes": attributes}, attributes)
    token = _active_langsmith_run.set(run_id) if run_id else None
    error: Exception | None = None
    try:
        yield
        outcome = "success"
    except Exception as exc:
        error = exc
        outcome = "error"
        raise
    finally:
        elapsed = time.monotonic() - started
        if _metrics:
            _metrics["requests"].labels(name, outcome).inc()
            _metrics["latency"].labels(name).observe(elapsed)
        if trace_span:
            trace_span.__exit__(type(error) if error else None, error,
                                error.__traceback__ if error else None)
        if run_id:
            finish_langsmith_run(run_id, {
                "outcome": outcome, "duration_ms": round(elapsed * 1000),
            }, error)
        if token is not None:
            _active_langsmith_run.reset(token)


@contextmanager
def langsmith_trace(name: str, run_type: str = "chain",
                    inputs: dict[str, Any] | None = None,
                    metadata: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """Trace a logical agent run and yield a dict for its output payload.

    A disabled or unavailable SDK yields the same no-op context, so callers
    do not need environment checks and the existing OTel/file logging path is
    unaffected.
    """
    context: dict[str, Any] = {"run_id": None, "outputs": {}}
    run_id = start_langsmith_run(name, run_type, inputs or {}, metadata)
    if run_id is None:
        yield context
        return

    context["run_id"] = run_id
    token = _active_langsmith_run.set(run_id)
    error: Exception | None = None
    try:
        yield context
    except Exception as exc:
        error = exc
        raise
    finally:
        finish_langsmith_run(run_id, context.get("outputs", {}), error)
        _active_langsmith_run.reset(token)
