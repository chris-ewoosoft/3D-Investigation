"""Graph facade — thin wrapper that delegates to LangGraphAgent.LocalAgentGraph.

Phase 4 migration strategy:
  1. This module exposes a ``LocalAgentGraph`` class that is API-compatible
     with the original in LangGraphAgent.py.
  2. It imports from the root LangGraphAgent module so the actual graph logic
     still runs unchanged. This lets us progressively migrate node code into
     the ``nodes/`` sub-package without breaking any callers.
  3. Once all nodes are migrated, this file will stop delegating and own the
     StateGraph construction directly.
"""
from __future__ import annotations

# Re-export from root module — backward-compatible shim
try:
    from LangGraphAgent import AgentState as _LGAgentState  # noqa: F401 — keep available
    from LangGraphAgent import LocalAgentGraph
except ImportError:
    # Fallback for test environments without langgraph installed
    LocalAgentGraph = None  # type: ignore[assignment,misc]

__all__ = ["LocalAgentGraph"]
