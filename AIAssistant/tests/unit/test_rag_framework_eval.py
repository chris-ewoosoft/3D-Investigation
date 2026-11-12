"""Pins the arithmetic and fail-closed gate around the RAG framework eval."""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
import warnings
from pathlib import Path
from typing import Any
from unittest.mock import patch

_SCRIPT = Path(__file__).resolve().parents[2] / "evals" / "run_rag_framework_eval.py"
_LOAD_ERROR = "cannot load RAG framework eval"
_HAS_EVAL_FRAMEWORKS = (
    importlib.util.find_spec("deepeval") is not None
    and importlib.util.find_spec("ragas") is not None
)


def _fake_framework_modules(warning_message: str) -> dict[str, types.ModuleType]:
    deepeval_mod = types.ModuleType("deepeval")
    deepeval_mod.assert_test = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    deepeval_metrics = types.ModuleType("deepeval.metrics")
    deepeval_metrics.BaseMetric = type("BaseMetric", (), {})  # type: ignore[attr-defined]
    deepeval_tc = types.ModuleType("deepeval.test_case")
    deepeval_tc.LLMTestCase = type("LLMTestCase", (), {})  # type: ignore[attr-defined]
    deepeval_mod.metrics = deepeval_metrics  # type: ignore[attr-defined]
    deepeval_mod.test_case = deepeval_tc  # type: ignore[attr-defined]

    ragas_mod = types.ModuleType("ragas")
    ragas_mod.SingleTurnSample = type("SingleTurnSample", (), {})  # type: ignore[attr-defined]

    class _RagasMetrics(types.ModuleType):
        def __getattr__(self, name: str) -> Any:
            if name in {"IDBasedContextPrecision", "IDBasedContextRecall"}:
                warnings.warn(warning_message, DeprecationWarning, stacklevel=2)
                return type(name, (), {})
            raise AttributeError(name)

    ragas_metrics = _RagasMetrics("ragas.metrics")
    ragas_mod.metrics = ragas_metrics  # type: ignore[attr-defined]
    return {
        "deepeval": deepeval_mod,
        "deepeval.metrics": deepeval_metrics,
        "deepeval.test_case": deepeval_tc,
        "ragas": ragas_mod,
        "ragas.metrics": ragas_metrics,
    }


def _load_eval():
    spec = importlib.util.spec_from_file_location("run_rag_framework_eval", _SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(_LOAD_ERROR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RetrievalF1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.eval = _load_eval()

    def test_exact_match_is_one(self) -> None:
        self.assertEqual(self.eval.retrieval_f1([1, 2], [1, 2]), 1.0)

    def test_partial_match_uses_precision_and_recall(self) -> None:
        self.assertAlmostEqual(self.eval.retrieval_f1([1, 2, 3, 4], [1, 2, 3]), 6 / 7)

    def test_empty_side_fails_closed(self) -> None:
        self.assertEqual(self.eval.retrieval_f1([], [1]), 0.0)
        self.assertEqual(self.eval.retrieval_f1([1], []), 0.0)


class FrameworkGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.eval = _load_eval()

    @staticmethod
    def _report(precision: float = 0.95, recall: float = 1.0, f1: float = 0.97):
        return {
            "mean_ragas_context_precision": precision,
            "mean_ragas_context_recall": recall,
            "mean_deepeval_retrieval_f1": f1,
            "cases": [{"deepeval_passed": True}],
        }

    def test_all_framework_floors_must_pass(self) -> None:
        self.assertTrue(self.eval.gate_passes(self._report(), 0.90, 0.95, 0.90))
        self.assertFalse(self.eval.gate_passes(self._report(precision=0.89), 0.90, 0.95, 0.90))
        self.assertFalse(self.eval.gate_passes(self._report(recall=0.94), 0.90, 0.95, 0.90))
        self.assertFalse(self.eval.gate_passes(self._report(f1=0.89), 0.90, 0.95, 0.90))

    def test_deepeval_case_failure_cannot_be_hidden_by_the_mean(self) -> None:
        report = self._report()
        report["cases"] = [{"deepeval_passed": True}, {"deepeval_passed": False}]
        self.assertFalse(self.eval.gate_passes(report, 0.90, 0.95, 0.90))

    def test_default_thresholds_are_release_floors(self) -> None:
        args = self.eval._parse_args([])
        self.assertEqual(
            (args.min_precision, args.min_recall, args.min_f1, args.min_case_f1),
            (0.90, 0.95, 0.90, 0.85),
        )

    def test_load_frameworks_suppresses_only_id_based_deprecation(self) -> None:
        suppressed = "Importing IDBasedContextPrecision from ragas.metrics is deprecated"
        globals().pop("__warningregistry__", None)
        with (
            patch.dict(sys.modules, _fake_framework_modules(suppressed)),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error", DeprecationWarning)
            frameworks = self.eval._load_frameworks()
        self.assertEqual(frameworks.precision_metric.__name__, "IDBasedContextPrecision")
        self.assertEqual(frameworks.recall_metric.__name__, "IDBasedContextRecall")

        unrelated = "Some other ragas deprecation warning"
        globals().pop("__warningregistry__", None)
        with (
            patch.dict(sys.modules, _fake_framework_modules(unrelated)),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("error", DeprecationWarning)
            with self.assertRaises(DeprecationWarning):
                self.eval._load_frameworks()

    @unittest.skipUnless(
        _HAS_EVAL_FRAMEWORKS,
        "requires ragas and deepeval (requirements-eval.lock)",
    )
    def test_pinned_framework_import_is_deprecation_clean(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            frameworks = self.eval._load_frameworks()
        self.assertEqual(frameworks.precision_metric.__name__, "IDBasedContextPrecision")
        self.assertEqual(frameworks.recall_metric.__name__, "IDBasedContextRecall")


if __name__ == "__main__":
    unittest.main()
