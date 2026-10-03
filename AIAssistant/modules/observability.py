"""Legacy observability shim.

This module now re-exports the implementation from `src/ai_assistant/observability`.
"""
from ai_assistant.observability import (
    get_langsmith_client,
    langsmith_available,
    langsmith_trace,
    prometheus_payload,
    record_approval,
    record_langsmith_feedback,
    record_schema_error,
    record_step_guard,
    record_token_usage,
    record_tool,
    span,
)

__all__ = [
    "get_langsmith_client",
    "langsmith_available",
    "langsmith_trace",
    "prometheus_payload",
    "record_approval",
    "record_langsmith_feedback",
    "record_schema_error",
    "record_step_guard",
    "record_token_usage",
    "record_tool",
    "span",
]
