"""Deterministic RAGAS + DeepEval regression gate for hybrid retrieval.

The gate deliberately uses no judge model or external API. RAGAS grades the
retrieved/reference chunk IDs with its native context precision and recall
metrics. DeepEval executes an offline custom retrieval-F1 metric through its
real ``LLMTestCase``/``assert_test`` runner. The system under test is the
production ``hybrid_retrieve`` function, not a duplicate scoring fixture.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_assistant.rag.domain import ChunkResult
from ai_assistant.rag.retrieval import hybrid_retrieve

CASES_PATH = Path(__file__).with_name("rag_retrieval_cases.json")
REPORT_PATH = Path(__file__).with_name("rag_framework_report.json")


@dataclass(frozen=True)
class Frameworks:
    single_turn_sample: type[Any]
    precision_metric: type[Any]
    recall_metric: type[Any]
    assert_test: Any
    base_metric: type[Any]
    test_case: type[Any]


class _Retriever:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def retrieve(self, _query: str) -> list[Any]:
        return [
            SimpleNamespace(
                node=SimpleNamespace(metadata={"chunk_index": int(row["id"])}),
                score=row.get("score"),
            )
            for row in self._rows
        ]


def _load_frameworks() -> Frameworks:
    from deepeval import assert_test
    from deepeval.metrics import BaseMetric
    from deepeval.test_case import LLMTestCase
    from ragas import SingleTurnSample

    # RAGAS 0.4.3 points at ``metrics.collections``, but that package does not
    # export the ID-based classes yet. Keep the only public working API and
    # suppress precisely this contradictory migration warning at the import
    # boundary; every other deprecation remains visible.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Importing IDBasedContext(?:Precision|Recall).*",
            category=DeprecationWarning,
        )
        from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall

    return Frameworks(
        single_turn_sample=SingleTurnSample,
        precision_metric=IDBasedContextPrecision,
        recall_metric=IDBasedContextRecall,
        assert_test=assert_test,
        base_metric=BaseMetric,
        test_case=LLMTestCase,
    )


def retrieval_f1(retrieved: list[int], reference: list[int]) -> float:
    """Return set-based retrieval F1 for DeepEval's deterministic metric."""
    retrieved_set = set(retrieved)
    reference_set = set(reference)
    if not retrieved_set or not reference_set:
        return 0.0
    overlap = len(retrieved_set & reference_set)
    precision = overlap / len(retrieved_set)
    recall = overlap / len(reference_set)
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def _metric_type(base_metric: type[Any]) -> type[Any]:
    class RetrievalF1Metric(base_metric):
        def __init__(self, threshold: float) -> None:
            self.threshold = threshold
            self.score = 0.0
            self.success = False
            self.reason = "not measured"
            self.error = None
            self.async_mode = False
            self.strict_mode = False
            self.verbose_mode = False

        def measure(self, test_case: Any, *_args: Any, **_kwargs: Any) -> float:
            retrieved = json.loads(test_case.actual_output)
            reference = json.loads(test_case.expected_output)
            self.score = retrieval_f1(retrieved, reference)
            self.success = self.score >= self.threshold
            self.reason = f"retrieval F1={self.score:.4f}, threshold={self.threshold:.4f}"
            return self.score

        async def a_measure(self, test_case: Any, *_args: Any, **_kwargs: Any) -> float:
            return self.measure(test_case)

        def is_successful(self) -> bool:
            return self.error is None and self.success

        @property
        def __name__(self) -> str:
            return "Deterministic retrieval F1"

    return RetrievalF1Metric


def _run_retrieval(case: dict[str, Any]) -> list[int]:
    all_ids = {
        int(row["id"]) for row in case["dense"]
    } | {int(chunk_id) for chunk_id in case["sparse"]} | {
        int(chunk_id) for chunk_id in case["reference"]
    }
    chunks = [
        ChunkResult(
            text=f"evaluation chunk {chunk_id}",
            source_path=f"eval/{chunk_id}",
            loader_type="eval",
            metadata={"chunk_index": chunk_id},
        )
        for chunk_id in range(max(all_ids) + 1)
    ]
    sparse_rows = [{"id": chunk_id, "score": None} for chunk_id in case["sparse"]]
    results = hybrid_retrieve(
        case["query"],
        None,
        _Retriever(case["dense"]),
        _Retriever(sparse_rows),
        chunks,
        similarity_threshold=0.30,
        final_k=int(case["result_k"]),
    )
    return [int(chunk.metadata["chunk_index"]) for chunk in results]


async def _score_case(
    case: dict[str, Any], frameworks: Frameworks, min_f1: float
) -> dict[str, Any]:
    retrieved = _run_retrieval(case)
    reference = [int(chunk_id) for chunk_id in case["reference"]]
    sample = frameworks.single_turn_sample(
        retrieved_context_ids=retrieved,
        reference_context_ids=reference,
    )
    precision = float(await frameworks.precision_metric().single_turn_ascore(sample))
    recall = float(await frameworks.recall_metric().single_turn_ascore(sample))

    metric = _metric_type(frameworks.base_metric)(min_f1)
    test_case = frameworks.test_case(
        input=case["query"],
        actual_output=json.dumps(retrieved),
        expected_output=json.dumps(reference),
    )
    deep_eval_passed = True
    try:
        frameworks.assert_test(test_case, [metric], run_async=False)
    except AssertionError:
        deep_eval_passed = False

    return {
        "name": case["name"],
        "retrieved": retrieved,
        "reference": reference,
        "ragas_context_precision": precision,
        "ragas_context_recall": recall,
        "deepeval_retrieval_f1": float(metric.score),
        "deepeval_passed": deep_eval_passed,
    }


def gate_passes(report: dict[str, Any], min_precision: float, min_recall: float, min_f1: float) -> bool:
    return (
        report["mean_ragas_context_precision"] >= min_precision
        and report["mean_ragas_context_recall"] >= min_recall
        and report["mean_deepeval_retrieval_f1"] >= min_f1
        and all(case["deepeval_passed"] for case in report["cases"])
    )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    frameworks = _load_frameworks()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    details = [await _score_case(case, frameworks, args.min_case_f1) for case in cases]
    count = len(details)
    return {
        "frameworks": {"ragas": version("ragas"), "deepeval": version("deepeval")},
        "total": count,
        "mean_ragas_context_precision": sum(row["ragas_context_precision"] for row in details) / count,
        "mean_ragas_context_recall": sum(row["ragas_context_recall"] for row in details) / count,
        "mean_deepeval_retrieval_f1": sum(row["deepeval_retrieval_f1"] for row in details) / count,
        "thresholds": {
            "precision": args.min_precision,
            "recall": args.min_recall,
            "f1": args.min_f1,
            "case_f1": args.min_case_f1,
        },
        "cases": details,
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-precision", type=float, default=0.90)
    parser.add_argument("--min-recall", type=float, default=0.95)
    parser.add_argument("--min-f1", type=float, default=0.90)
    parser.add_argument("--min-case-f1", type=float, default=0.85)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except ImportError as exc:
        print(f"RAG framework eval dependencies are missing: {exc}", file=sys.stderr)
        return 2
    passed = gate_passes(report, args.min_precision, args.min_recall, args.min_f1)
    report["passed"] = passed
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
