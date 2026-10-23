"""The port layer's own helpers — the contracts every adapter implements.

The event cursor is the one place where a *duck-typed* store is supported:
a store that predates the cursor parameter still has to work. The
fallback used to be detected by catching the ``TypeError`` the call raised,
which also caught a ``TypeError`` raised inside the store and then called the
store a second time with the old signature. The signature is asked instead,
and a callable whose signature cannot be read is treated as one that does
not take the cursor — the safe direction, because the fallback works for
every store.
"""
from __future__ import annotations

import unittest

from ai_assistant.ports import store_supports_cursor


class _UnreadableSignature:
    """A callable whose signature inspection always fails."""

    def __init__(self, error: type[Exception]) -> None:
        self._error = error

    @property
    def __signature__(self) -> object:
        raise self._error

    def __call__(self, task_id: str) -> list[dict[str, object]]:
        return []


class StoreSupportsCursorTests(unittest.TestCase):
    def test_a_store_that_offers_the_cursor_is_recognised(self) -> None:
        def events(task_id: str, *, since: int = 0) -> list[dict[str, object]]:
            return []

        self.assertTrue(store_supports_cursor(events))

    def test_a_store_without_the_cursor_is_recognised(self) -> None:
        def events(task_id: str) -> list[dict[str, object]]:
            return []

        self.assertFalse(store_supports_cursor(events))

    def test_a_callable_without_a_signature_is_treated_as_without_a_cursor(self) -> None:
        """``inspect`` cannot read every callable's signature.

        The assumption must be the safe one: the caller falls back to the
        slice-by-count path, which works for every store.
        """
        for error in (TypeError, ValueError):
            with self.subTest(error=error.__name__):
                self.assertFalse(store_supports_cursor(_UnreadableSignature(error)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
