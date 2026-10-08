"""Compatibility runner for test_action_manifest."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from tests.unit.test_action_manifest import ManifestContractTests  # noqa: F401

if __name__ == "__main__":
    unittest.main()
