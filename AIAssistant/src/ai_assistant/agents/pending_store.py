"""In-memory pending-action store: the agent layer's own default (ADR 0003).

The file-backed implementation lives in ``adapters.persistence`` and is
injected by the composition root behind the ``PendingActionPort`` protocol.
This module used to *re-export that adapter*, which put the only
``agents → adapters`` edge in the codebase behind a docstring claiming the
boundary was "enforced by boundary tests". The dependency is gone instead of
being enforced: when no store is injected, services fall back to this
dict-backed port implementation — memory only, no file IO — so the agent
layer keeps its Clean Architecture position.
"""
from __future__ import annotations

import threading
from typing import Any

from ..domain.pending import prune_expired


class MemoryPendingStore:
    """Dict-backed ``PendingActionPort`` with no persistence.

    Equivalent to the adapter's empty-path mode: state lives for the life of
    the process only, which is exactly what a bare ``AgentService()`` (tests,
    embeds) should get. ``load``/``save`` are deliberate no-ops so the run
    paths can call them unconditionally.
    """

    def __init__(self) -> None:
        #: No file backs this store; ``reset_state``'s file removal is skipped.
        self.path = ""
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = value

    def get(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get(key, default)

    def pop(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._lock:
            return self._data.pop(key, default)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def load(self) -> None:
        """No-op: memory state intentionally does not survive a restart."""

    def save(self) -> None:
        """No-op: memory state intentionally does not survive a restart."""

    def cleanup(self, cutoff: float) -> bool:
        # Same domain rule as the file-backed adapter (domain/pending.py).
        with self._lock:
            return bool(prune_expired(self._data, cutoff))


__all__ = ["MemoryPendingStore"]
