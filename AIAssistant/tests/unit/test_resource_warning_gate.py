"""The full-suite runner must make leaked resources a red gate."""
from __future__ import annotations

import io
import unittest
import warnings

from tests.run_suite import run_loaded_suite


class ResourceWarningGateTests(unittest.TestCase):
    @staticmethod
    def _suite(case: unittest.TestCase) -> unittest.TestSuite:
        return unittest.TestSuite([case])

    def test_a_clean_suite_passes(self) -> None:
        case = unittest.FunctionTestCase(lambda: None)
        self.assertEqual(run_loaded_suite(self._suite(case), io.StringIO()), 0)

    def test_a_resource_warning_fails_the_process_gate(self) -> None:
        def leak() -> None:
            warnings.warn("unclosed demonstration handle", ResourceWarning, stacklevel=1)

        stream = io.StringIO()
        self.assertEqual(
            run_loaded_suite(self._suite(unittest.FunctionTestCase(leak)), stream),
            1,
        )
        self.assertIn("RESOURCE LEAK GATE FAILED", stream.getvalue())

    def test_an_ordinary_failure_still_fails(self) -> None:
        def fail() -> None:
            raise AssertionError

        self.assertEqual(
            run_loaded_suite(self._suite(unittest.FunctionTestCase(fail)), io.StringIO()),
            1,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
