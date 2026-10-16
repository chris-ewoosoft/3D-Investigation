"""Shared policy for pending approval/UI-action entries (ADR 0003).

Two ``PendingActionPort`` implementations exist: the file-backed adapter in
``adapters.persistence`` and the agent layer's in-memory fallback. Both
expire parked actions the same way, so the pruning rule lives here once —
pure stdlib, no IO, in keeping with the domain layer's purity boundary.
"""
from __future__ import annotations

from typing import Any


def prune_expired(data: dict[str, dict[str, Any]], cutoff: float) -> list[str]:
    """Delete entries whose ``created_at`` predates ``cutoff``.

    Returns the removed keys so the caller can decide whether the deletion is
    worth persisting. Entries without a ``created_at`` stamp are kept: an
    unknown age must never silently delete a live approval.
    """
    expired = [
        key
        for key, value in data.items()
        if (created_at := value.get("created_at")) is not None and created_at < cutoff
    ]
    for key in expired:
        del data[key]
    return expired
