"""Inference adapter used while llama.cpp runtime migration is in progress."""
from __future__ import annotations

from collections.abc import Callable


class LegacyConstrainedCompletion:
    """Keeps llama.cpp specifics outside the application agent loop."""

    def __init__(self, complete: Callable[[list[dict], int, float], str]) -> None:
        self._complete = complete

    def __call__(self, messages: list[dict[str, str]], temperature: float) -> str:
        return self._complete(messages, 2048, temperature)
