"""Model-in-the-loop eval over a fixed golden set (Sprint 2).

Honest by construction (ADR 0015):

* the golden prompts are literals in this file, written against the planner's
  documented three-way vocabulary (``direct`` / ``rag_search`` /
  ``application_action``), not derived from the code under test;
* grading runs the *real* planner — ``PLANNER_PROMPT`` through the loaded
  backend — and parses the JSON the planner is asked for, instead of a rule
  classifier;
* with no backend loaded the script prints ``SKIP`` and exits 0 **without**
  reporting an accuracy figure — unless a gate was requested: a run asked to
  ``--min-model-coverage`` must fail without a model, because passing having
  graded nothing would make the gate a lie. The previous version called a rule
  classifier (since deleted as dead code) whose vocabulary
  (``coding``/``rag``/``direct``) could never equal these expectations, so
  every run produced a number that was wrong in one direction or the other.

Gates (``--min-model-coverage``, ``--min-accuracy``) decide the exit code of a
graded run; without them the historical contract stands (every case must pass).
``--baseline`` additionally compares the measured numbers with a stored report
and fails on a drop larger than ``--baseline-tolerance`` — that is the
model-version regression gate, and it is an input: an unreadable baseline file
fails the run rather than skipping the comparison. Either way the measured
numbers are printed *and* written to ``evals/model_in_loop_report.json`` — a
green run can never hide a low figure.

Tool selection is graded separately by ``run_tool_eval.py``; this file grades
only the planner's step kind and its p95 latency.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
# The package lives under ``src/`` (ADR 0010 layout).
sys.path.insert(0, str(ROOT / "src"))

#: The graded run's report — uploaded as a CI artifact by agent-evals.yml.
REPORT_PATH = Path(__file__).resolve().parent / "model_in_loop_report.json"

GOLDEN = [
    {"id": "vn_001", "lang": "vi", "query": "Dự án này có chức năng gì?",
     "expected_kind": "rag_search"},
    {"id": "vn_002", "lang": "vi", "query": "Tìm lỗi trong file src/core/AppConstants.h",
     "expected_kind": "rag_search"},
    {"id": "vn_003", "lang": "vi", "query": "Mở trình xem mô hình 3D",
     "expected_kind": "application_action"},
    {"id": "vn_004", "lang": "vi", "query": "Cảm ơn bạn",
     "expected_kind": "direct"},
    {"id": "en_001", "lang": "en", "query": "What is the benefit of AI Agent?",
     "expected_kind": "direct"},
    {"id": "en_002", "lang": "en", "query": "Build the 3D reconstruction pipeline",
     "expected_kind": "application_action"},
    {"id": "en_003", "lang": "en", "query": "Where is the RAG index stored?",
     "expected_kind": "rag_search"},
]

# ``run_fn(query) -> (kind, latency_seconds)``: injected so the grading logic
# itself is testable without a 4 GB model on the machine.
RunFn = Callable[[str], tuple[Any, float]]


def _response_content(response: Any) -> str:
    """Read ``choices[0].message.content`` from a dict or an object payload."""
    if isinstance(response, dict):
        choices = response.get("choices") or [{}]
        message = choices[0].get("message", {})
        return str(message.get("content", ""))
    choices = getattr(response, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    return str(getattr(message, "content", "") or "")


def _grade_planner_payload(raw: str) -> Any:
    """Extract the first declared step kind from one raw planner answer.

    Mirrors the production parse (``orchestration/nodes/plan.py``:
    ``strip_think_tags`` + ``json.loads`` with a decode failure meaning "no
    plan"), so what grades here is exactly what the pipeline would consume:
    an answer that is not JSON — fenced, truncated, prose — declares no kind
    and is reported as ungradeable instead of crashing the run.
    """
    from ai_assistant.llm.inference import strip_think_tags

    try:
        payload = json.loads(strip_think_tags(raw))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    kinds = payload.get("step_kinds")
    if not isinstance(kinds, list) or not kinds or not isinstance(kinds[0], str):
        return None
    return kinds[0]


def planner_run(query: str, backend: Any, *, max_tokens: int = 384,
                temperature: float = 0.0) -> tuple[Any, float]:
    """Ask the loaded backend the planner question and parse its step kinds."""
    from ai_assistant.orchestration.prompts import PLANNER_PROMPT

    started = time.monotonic()
    response = backend.generate(
        messages=[
            {"role": "system", "content": PLANNER_PROMPT},
            {"role": "user", "content": f"Yêu cầu hiện tại: {query}"},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    latency = time.monotonic() - started

    # A single-step answer still has to declare its kind; an unparseable
    # answer is an ungradeable one and is reported as ``None`` rather than
    # guessed (SCORECARD #1 counts it as not model-graded).
    return _grade_planner_payload(_response_content(response)), latency


def run_golden(run_fn: RunFn, cases: list[dict] | None = None) -> dict:
    """Grade the planner against the golden set.

    ``accuracy`` is kind accuracy over all cases. ``model_coverage`` is the
    SCORECARD #1 signal: the fraction of cases the model actually produced a
    gradeable (non-``None``) step kind for. A backend that returns empty or
    unparseable ``step_kinds`` for some prompts drops coverage below 100% even
    though a model loaded — which is the honest reading, because those cases
    were never really exercised.
    """
    results = []
    for item in cases or GOLDEN:
        kind, latency = run_fn(item["query"])
        results.append({
            "id": item["id"],
            "ok": kind == item["expected_kind"],
            "expected": item["expected_kind"],
            "kind": kind,
            "latency": latency,
        })
    total = len(results)
    passed = sum(1 for result in results if result["ok"])
    graded = sum(1 for result in results if result["kind"] is not None)
    latencies = sorted(result["latency"] for result in results)
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0
    return {
        "total": total,
        "passed": passed,
        "accuracy": (passed / total) if total else 0.0,
        "model_coverage": (graded / total) if total else 0.0,
        "p95_latency": p95,
        "results": results,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade the planner on the golden set.")
    parser.add_argument(
        "--model-path",
        help="Load this GGUF through LlamaCppBackend instead of the already-loaded backend.",
    )
    parser.add_argument(
        "--min-model-coverage", type=float, metavar="FRACTION",
        help="Gate: fail unless this fraction of the golden set was model-graded (e.g. 0.9).",
    )
    parser.add_argument(
        "--min-accuracy", type=float, metavar="FRACTION",
        help="Gate: fail unless kind accuracy reaches this fraction (default: report only).",
    )
    parser.add_argument(
        "--baseline", metavar="PATH",
        help=("Report from an earlier graded run. The accuracy and model_coverage "
              "of this run may not fall more than --baseline-tolerance below it "
              "(model-version regression gate)."),
    )
    parser.add_argument(
        "--baseline-tolerance", type=float, default=0.05, metavar="FRACTION",
        help="Allowed drop below the baseline before it counts as a regression (default 0.05).",
    )
    return parser.parse_args(argv)


def _load_backend(args: argparse.Namespace) -> Any | None:
    """Resolve the backend: an explicit GGUF path, else the loaded singleton."""
    if args.model_path:
        from ai_assistant.llm.llama_cpp_backend import LlamaCppBackend
        return LlamaCppBackend(model_path=args.model_path)
    from ai_assistant.llm.model_loader import get_backend
    return get_backend()


def _baseline_failures(result: dict, baseline: dict, tolerance: float) -> list[str]:
    """Compare this graded run with a stored report (model-version regression).

    A different model build can keep the absolute floors green while the
    measured accuracy drops several points; this gate reads the *previous*
    report and fails on that drop. Only metrics present in the baseline are
    compared, so older reports stay usable.
    """
    failures: list[str] = []
    for metric in ("accuracy", "model_coverage"):
        if metric not in baseline:
            continue
        previous = float(baseline[metric])
        drop = previous - float(result[metric])
        # Float subtraction must not turn "exactly at the tolerance" into a
        # regression (1.0 - 0.95 is 0.050000000000000044 in binary floats).
        if drop - tolerance > 1e-9:
            failures.append(
                f"{metric}={result[metric]:.0%} is {drop:.0%} below the "
                f"baseline {previous:.0%} (tolerance {tolerance:.0%})"
            )
    return failures


def _gate_exit(result: dict, args: argparse.Namespace,
               baseline_failures: list[str] | None = None) -> int:
    """Exit code for a graded run.

    With no gate flag the historical contract stands (every case must pass);
    with one, the requested floors decide — and a failing floor prints the
    measured number next to the requirement, so the log always states what was
    actually observed (ADR 0015: report, never fabricate).
    """
    failures = list(baseline_failures or [])
    if args.min_model_coverage is None and args.min_accuracy is None and not failures:
        return 0 if result["passed"] == result["total"] else 1
    if args.min_model_coverage is not None and result["model_coverage"] < args.min_model_coverage:
        failures.append(
            f"model_coverage={result['model_coverage']:.0%} < required {args.min_model_coverage:.0%}"
        )
    if args.min_accuracy is not None and result["accuracy"] < args.min_accuracy:
        failures.append(f"accuracy={result['accuracy']:.0%} < required {args.min_accuracy:.0%}")
    for failure in failures:
        print(f"GATE FAIL: {failure}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    baseline: dict | None = None
    if args.baseline:
        # A regression baseline is an input, not a suggestion: an unreadable
        # file fails the run instead of silently skipping the comparison.
        try:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"BASELINE FAIL: cannot read {args.baseline}: {error}", file=sys.stderr)
            return 2
        if not isinstance(baseline, dict):
            print("BASELINE FAIL: baseline report must be a JSON object", file=sys.stderr)
            return 2
    backend = _load_backend(args)
    if backend is None:
        # No model: report the skip, never a synthetic 0% or 100%. SCORECARD #1
        # ("tỉ lệ eval có model") reads 0 here — this run exercised no model.
        # Requesting a gate without a model fails instead: CI going green while
        # having graded nothing would be the exact dishonesty this script exists
        # to prevent.
        print("SKIP: no backend loaded — pass --model-path (or load a model) first.")
        print("model_coverage=0.00 (no backend; 0 of the golden set was model-graded)")
        gated = (args.min_model_coverage is not None or args.min_accuracy is not None
                 or baseline is not None)
        return 1 if gated else 0

    result = run_golden(lambda query: planner_run(query, backend))
    result["model"] = str(
        args.model_path or getattr(backend, "model_description", "loaded backend")
    )
    baseline_failures = (
        _baseline_failures(result, baseline, args.baseline_tolerance) if baseline else []
    )
    REPORT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"model={result['model']}")
    print(
        f"kind accuracy={result['accuracy']:.0%} "
        f"({result['passed']}/{result['total']}), "
        f"model_coverage={result['model_coverage']:.0%}, "
        f"p95_latency={result['p95_latency']:.3f}s"
    )
    print(json.dumps(result["results"], ensure_ascii=False, indent=2))
    return _gate_exit(result, args, baseline_failures)


if __name__ == "__main__":
    raise SystemExit(main())
