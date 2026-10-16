"""Thread-safe, dict-compatible persistence store for pending approvals and UI actions."""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ai_assistant.domain.pending import prune_expired
from ai_assistant.domain.steps import validate_steps

logger = logging.getLogger("ai_assistant.persistence.pending_store")


class PendingActionStore:
    """Thread-safe, dict-compatible persistence store for pending approvals and UI actions."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, Any]] = {}

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def __getitem__(self, key: str) -> dict[str, Any]:
        with self._lock:
            return self._data[key]

    def get(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Peek an entry without taking ownership of it (read-only copy source).

        ``AgentService.ui_action_result`` uses this to learn the parked
        action's session before it can pop the entry — the request id alone
        does not carry the session, and the run lock has to be the right one.
        """
        with self._lock:
            return self._data.get(key, default)

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = value

    def __delitem__(self, key: str) -> None:
        with self._lock:
            del self._data[key]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._data.keys()))

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def pop(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self._lock:
            return self._data.pop(key, default)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def load(self) -> None:
        with self._lock:
            try:
                if not self._path.exists():
                    return
                with open(self._path, encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    for key, value in payload.items():
                        if not isinstance(value, dict):
                            continue
                        if "steps" in value:
                            try:
                                value["steps"] = validate_steps(value["steps"])
                            except ValueError as error:
                                # The one trust crossing of the step vocabulary
                                # (ADR 0019): a persisted entry whose log does
                                # not parse is dropped whole — half an approval
                                # context must not reach resume — and the
                                # refusal stays visible like any other load
                                # failure (finding 25).
                                logger.warning(
                                    "Dropping pending action %s from %s: %s",
                                    key, self.path, error,
                                )
                                continue
                        self._data[str(key)] = value
            except (OSError, ValueError) as error:
                # Dropping to an empty store is deliberate (a corrupt file must
                # not brick startup) but it must not be silent: the operator needs
                # to know pending approvals vanished (finding 25).
                logger.warning("Pending-action state could not be read from %s: %s", self.path, error)

    def save(self) -> None:
        with self._lock:
            try:
                if not self._path.name:
                    return
                self._path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = self._path.with_suffix(".tmp")
                with open(temp_path, "w", encoding="utf-8") as handle:
                    json.dump(dict(self._data), handle, ensure_ascii=False, indent=2, default=str)
                temp_path.replace(self._path)
            except OSError as error:
                # The API still answers pending_approval after this, so the
                # refusal to persist has to be visible in the log (finding 25).
                logger.warning("Pending-action state could not be written to %s: %s", self.path, error)

    def cleanup(self, cutoff: float) -> bool:
        # The expiry rule is domain policy shared with the agent layer's
        # in-memory fallback (domain/pending.py), so both expire identically.
        with self._lock:
            return bool(prune_expired(self._data, cutoff))
