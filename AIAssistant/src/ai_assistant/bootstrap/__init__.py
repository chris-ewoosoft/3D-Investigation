"""Composition-root package: DI container and app factory.

Importing this package binds nothing: the container and the FastAPI factory
load on first attribute access, so ``import ai_assistant.bootstrap.server``
pulls no third-party framework until ``compose()`` runs (ADR 0022).
"""
from __future__ import annotations

import importlib
from typing import Any

_LAZY_ATTRIBUTES: dict[str, tuple[str, str]] = {
    "PlatformContainer": (".container", "PlatformContainer"),
    "build_container": (".container", "build_container"),
    "create_app": (".app_factory", "create_app"),
}

__all__ = ["PlatformContainer", "build_container", "create_app"]


def __getattr__(name: str) -> Any:
    """Load a composed name from its owning submodule on first access."""
    try:
        module_suffix, attr = _LAZY_ATTRIBUTES[name]
    except KeyError:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from None
    return getattr(importlib.import_module(module_suffix, __name__), attr)
