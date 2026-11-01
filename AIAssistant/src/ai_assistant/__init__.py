"""3D-Reconstruction AI Agent Platform.

The package intentionally keeps domain and application code independent from
FastAPI, LangGraph, MCP and model-provider SDKs.  Those technologies live in
adapters and are wired only by the composition root.
"""

from .settings import ArchitectureSettings

__all__ = ["ArchitectureSettings"]
