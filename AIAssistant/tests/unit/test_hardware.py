"""Unit tests for GPU/memory cleanup helpers.

``torch`` is not part of the offline CI dependency set, so the tests inject a
fake module through ``sys.modules``. Cleanup must never import an unloaded ML
stack merely to release it.
"""
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from ai_assistant.llm import hardware


class ReleaseMlMemoryTest(unittest.TestCase):
    def test_degrades_when_torch_missing(self) -> None:
        with (
            mock.patch.dict(sys.modules, {"torch": None}),
            mock.patch.object(hardware.gc, "collect") as collect,
        ):
            hardware.release_ml_memory()  # must not raise
        # Only the CUDA half degrades to silence: the collector still ran.
        collect.assert_called_once()

    def test_does_not_import_torch_during_teardown(self) -> None:
        with (
            mock.patch.dict(sys.modules, {"torch": None}),
            mock.patch("builtins.__import__", wraps=__import__) as import_module,
        ):
            hardware.release_ml_memory()
        self.assertFalse(
            any(call.args and call.args[0] == "torch" for call in import_module.mock_calls)
        )

    def test_clears_cuda_cache_when_available(self) -> None:
        empty_cache = mock.Mock()
        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True, empty_cache=empty_cache)
        )
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            hardware.release_ml_memory()
        empty_cache.assert_called_once()

    def test_skips_cache_clear_without_cuda(self) -> None:
        empty_cache = mock.Mock()
        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False, empty_cache=empty_cache)
        )
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            hardware.release_ml_memory()
        empty_cache.assert_not_called()

    def test_contains_cuda_probe_failure(self) -> None:
        fake_torch = SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=mock.Mock(side_effect=ValueError("driver unavailable")),
                empty_cache=mock.Mock(),
            )
        )
        with mock.patch.dict(sys.modules, {"torch": fake_torch}):
            hardware.release_ml_memory()  # teardown must remain best-effort
        fake_torch.cuda.empty_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
