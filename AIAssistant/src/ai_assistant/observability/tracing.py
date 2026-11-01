"""OpenTelemetry tracing and unified context managers.

The OTel tracer and the Prometheus registry are both built lazily on first use,
so importing this module never reads the process environment (ADR 0001). A
disabled or unavailable SDK yields a no-op span identical to the current API.
When ``AGENT_OBSERVABILITY=1`` *and* ``AGENT_OTLP_ENDPOINT`` points at a
collector (and the exporter package is installed) the tracer is given a
``BatchSpanProcessor`` so spans actually leave the process; every other
combination records nothing and never raises into the call path.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from ai_assistant.settings import observability_enabled, otlp_exporter_endpoint

from .langsmith_integration import (
    _active_langsmith_run,
    finish_langsmith_run,
    start_langsmith_run,
)
from .metrics import get_metrics, record_latency

logger = logging.getLogger("ai_assistant.observability.tracing")

_tracer_lock = threading.Lock()
# ``False`` is the "disabled" sentinel; otherwise holds the OTel Tracer (or None).
_tracer: Any = None


def _build_tracer() -> Any:
    """Resolve the tracer, wiring the OTLP exporter when one is configured."""
    from opentelemetry import trace

    endpoint = otlp_exporter_endpoint()
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError:
            logger.warning(
                "AGENT_OTLP_ENDPOINT is set but opentelemetry-exporter-otlp is "
                "not installed — spans stay in-process"
            )
        else:
            provider = TracerProvider()
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
            )
            trace.set_tracer_provider(provider)
            logger.info("OTLP span export enabled -> %s", endpoint)
    return trace.get_tracer("3d-reconstruction.agent")


def _get_tracer() -> Any:
    """Return the cached OTel tracer, or ``None`` when observability is off."""
    global _tracer
    if _tracer is None:
        with _tracer_lock:
            if _tracer is None:
                _tracer = False
                if observability_enabled():
                    try:
                        _tracer = _build_tracer()
                    except ImportError:
                        _tracer = False
    return _tracer


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Emit an OpenTelemetry span and a nested LangSmith run when enabled."""
    started = time.monotonic()
    tracer = _get_tracer()
    trace_span = tracer.start_as_current_span(name) if tracer else None
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
        record_latency(name, elapsed)
        metrics = get_metrics()
        if metrics:
            metrics["requests"].labels(name, outcome).inc()
            metrics["latency"].labels(name).observe(elapsed)
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


def current_traceparent() -> str | None:
    """The W3C ``traceparent`` for the active span, or ``None`` outside one.

    Lets an outbound call (the A2A client) carry the trace across the process
    boundary so a remote agent's spans can join the same trace. With no OTel
    SDK importable, observability off, or no active span there is nothing
    valid to propagate — ``None`` is returned rather than a fabricated id, so
    the caller omits the header instead of starting a bogus trace.
    """
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    trace_flags = getattr(context, "trace_flags", 0)
    return f"00-{context.trace_id:032x}-{context.span_id:016x}-{trace_flags:02x}"


_TRACEPARENT_PATTERN = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def _parse_traceparent(header: str | None) -> tuple[int, int, int] | None:
    """Parse a W3C ``traceparent`` into ``(trace_id, span_id, flags)``.

    Only the version this service emits (``00``) is accepted; all-zero ids are
    rejected because the spec marks them invalid. Anything else — absent,
    malformed, a future version — returns ``None`` so the caller treats the
    header as absent rather than guessing.
    """
    if not header:
        return None
    match = _TRACEPARENT_PATTERN.match(header.strip())
    if match is None:
        return None
    trace_id = int(match.group(1), 16)
    span_id = int(match.group(2), 16)
    if trace_id == 0 or span_id == 0:
        return None
    return trace_id, span_id, int(match.group(3), 16)


@contextmanager
def attach_inbound_traceparent(header: str | None) -> Iterator[None]:
    """Join the caller's trace for one inbound request.

    ``current_traceparent`` is the outbound half — the A2A client attaching
    the header on the way out. This is the inbound half: a valid W3C
    ``traceparent`` becomes a remote, non-recording parent span attached to
    the OTel context, so every span created inside the block — including the
    A2A worker thread's, which inherits the context through the
    ``contextvars`` snapshot in ``application.tasks.TaskService`` — joins the
    caller's trace instead of starting a new root.

    A missing header, a malformed one, or an SDK that is not importable all
    yield a no-op context: propagation may fail, the request may not.
    """
    parsed = _parse_traceparent(header)
    if parsed is None:
        yield
        return
    try:
        from opentelemetry import context as otel_context
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            TraceFlags,
            set_span_in_context,
        )
    except ImportError:
        yield
        return
    trace_id, span_id, flags = parsed
    parent = set_span_in_context(
        NonRecordingSpan(
            SpanContext(trace_id=trace_id, span_id=span_id,
                        is_remote=True, trace_flags=TraceFlags(flags))
        )
    )
    token = otel_context.attach(parent)
    try:
        yield
    finally:
        otel_context.detach(token)
