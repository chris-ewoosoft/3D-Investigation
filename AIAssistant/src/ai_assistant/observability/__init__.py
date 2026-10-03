"""Observability package for the AI Assistant.

Provides metrics, tracing, LangSmith integrations, and logging infrastructure.
"""
from .langsmith_integration import (
    get_langsmith_client,
    langsmith_available,
    record_langsmith_feedback,
)
from .metrics import (
    prometheus_payload,
    record_approval,
    record_schema_error,
    record_step_guard,
    record_token_usage,
    record_tool,
)
from .tracing import langsmith_trace, span

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
