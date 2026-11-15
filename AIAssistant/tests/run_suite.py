"""Unittest entry point that turns leaked resources into a failing gate.

``ResourceWarning`` is ignored by CPython by default, and changing it to an
exception is insufficient for warnings raised from ``__del__``: those become
unraisable exceptions after the test has already passed.  This runner records
both warning channels, forces a final collection while the hooks are active,
and returns a failing process status when either channel reports a leaked
resource.
"""
from __future__ import annotations

import gc
import sys
import unittest
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO


def run_loaded_suite(suite: unittest.TestSuite, stream: TextIO) -> int:
    """Run ``suite`` and fail when a ResourceWarning escapes its owner."""
    unraisable: list[sys.UnraisableHookArgs] = []
    previous_hook = sys.unraisablehook

    def capture_unraisable(args: sys.UnraisableHookArgs) -> None:
        if args.exc_type is not None and issubclass(args.exc_type, ResourceWarning):
            unraisable.append(args)
            return
        previous_hook(args)

    try:
        sys.unraisablehook = capture_unraisable
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
            gc.collect()
        resource_warnings = [
            warning for warning in caught
            if issubclass(warning.category, ResourceWarning)
        ]
    finally:
        sys.unraisablehook = previous_hook

    if resource_warnings or unraisable:
        stream.write("\nRESOURCE LEAK GATE FAILED\n")
        for warning in resource_warnings:
            stream.write(f"  {warning.filename}:{warning.lineno}: {warning.message}\n")
        for item in unraisable:
            stream.write(f"  unraisable ResourceWarning: {item.exc_value}\n")
        return 1
    return 0 if result.wasSuccessful() else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Discover the repository suite and run it under the resource gate."""
    del argv  # Reserved for future test selection without changing the gate.
    tests_dir = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(tests_dir), pattern="test_*.py")
    return run_loaded_suite(suite, sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
