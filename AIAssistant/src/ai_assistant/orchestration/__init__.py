"""Orchestration package for LangGraph-based agent loop.

This package re-organises LangGraphAgent.py (1,731 LOC) into focused modules.
The root LangGraphAgent.py file remains as a compatibility shim that re-exports
``LocalAgentGraph`` and ``AgentState`` so existing imports are not broken.
"""
from .graph import LocalAgentGraph
from .state import AgentState

__all__ = ["AgentState", "LocalAgentGraph"]
