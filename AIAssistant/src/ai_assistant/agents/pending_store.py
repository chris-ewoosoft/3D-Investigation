"""Pending-action store for approval/UI-ACK workflows."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_assistant.agents.pending_store")


class PendingActionStore:
    """Thread-safe dict-backed store for agent approval state with persistence."""

    def __init__(self, file_path: str | Path) -> None:
        self._path = Path(file_path)
        self._lock = threading.Lock()
        self._store: dict[str, Any] = {}

    # ── dict-like API ──────────────────────────────────────────────────────────

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._store[key]

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._store

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._store.pop(key, default)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    @property
    def knowledge_chunks(self) -> bool:
        """Compatibility shim used by rag_tools."""
        return True  # Always True; store itself is always ready

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = dict(self._store)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        except OSError as error:
            logger.warning("Could not save pending actions: %s", error)

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._store.update(data)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("Could not load pending actions: %s", error)

    def cleanup(self, older_than: float) -> bool:
        """Remove entries created before ``older_than`` epoch seconds. Returns True if any removed."""
        with self._lock:
            expired = [
                key for key, value in self._store.items()
                if isinstance(value, dict) and value.get("created_at", time.time()) < older_than
            ]
            for key in expired:
                del self._store[key]
        return bool(expired)
