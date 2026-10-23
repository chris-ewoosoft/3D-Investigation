"""Bounded per-session run locks.

The old service kept a ``dict[str, RLock]`` for the life of the process. The
comment claimed pruning was unsafe because a second lock could be built while
an older thread was queued on the first. That is true for a *cache eviction*,
but not for reference counting: a thread increments the entry's user count
**before** it waits on the lock, so the last user cannot remove the entry until
every holder and every queued thread has left. When the count reaches zero the
entry is dropped; a later run gets a fresh lock, and no two runs can overlap.

The map is therefore bounded by the sessions that are live right now, not by
every session id the process has ever seen.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class _LockEntry:
    lock: threading.RLock
    users: int = 0


class SessionLockPool:
    """Reference-counted pool of per-session re-entrant locks."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, _LockEntry] = {}

    @contextmanager
    def hold(self, session_id: str) -> Iterator[None]:
        """Serialize work for ``session_id`` for the duration of the block.

        The reference is taken under the guard before waiting.  A queued
        thread is a user, so an idle entry can only be removed by the last
        user leaving, never by an unrelated pruning pass.
        """
        with self._guard:
            entry = self._entries.get(session_id)
            if entry is None:
                entry = _LockEntry(threading.RLock())
                self._entries[session_id] = entry
            entry.users += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._guard:
                entry.users -= 1
                if entry.users == 0:
                    self._entries.pop(session_id, None)

    def active_sessions(self) -> int:
        """Number of session entries currently held or queued (for tests)."""
        with self._guard:
            return len(self._entries)


__all__ = ["SessionLockPool"]
