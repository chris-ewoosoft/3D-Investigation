"""Persistence boundary for approval-gated Agent actions."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator, MutableMapping
from typing import Any


class PendingActionStore(MutableMapping[str, dict[str, Any]]):
    """Dict-compatible store so legacy routes can migrate incrementally."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}

    def __getitem__(self, key: str) -> dict[str, Any]:
        return self._data[key]

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                self._data.update({str(key): value for key, value in payload.items() if isinstance(value, dict)})
        except (OSError, ValueError):
            return

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temp_path = self.path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(dict(self._data), handle, ensure_ascii=False)
            os.replace(temp_path, self.path)
        except OSError:
            return

    def cleanup(self, cutoff: float) -> bool:
        expired = [key for key, value in self._data.items()
                   if value.get("created_at") is not None
                   and value.get("created_at") < cutoff]
        for key in expired:
            del self._data[key]
        return bool(expired)
