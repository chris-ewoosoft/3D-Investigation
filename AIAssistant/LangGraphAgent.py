"""Compatibility shim for LangGraphAgent.

The implementation has been migrated to `ai_assistant.orchestration.graph`.
This module remains for backwards compatibility with existing consumers and tests.
"""
from __future__ import annotations

from ai_assistant.orchestration.graph import (
    LocalAgentGraph,
    _summarize_messages,
)
from ai_assistant.orchestration.state import AgentState

__all__ = [
    "AgentState",
    "LocalAgentGraph",
    "_summarize_messages",
]
