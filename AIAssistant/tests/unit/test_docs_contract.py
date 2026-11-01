"""Documentation counts are part of the contract (ADR 0015 / ADR 0032).

The README, RUNBOOK, pyproject and the scorecard make numeric claims that a
reader cannot verify without re-running the tools. This test re-derives the
claims that can be derived from the tree and from the eval itself, and
cross-checks the measured claims the docs share, so a new ADR, source file,
eval case or gate run cannot leave one document silently stale.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
SCORECARD = ROOT / "Docs" / "SCORECARD.md"
ADR_DIR = ROOT / "Docs" / "adr"


class AdrIndexTests(unittest.TestCase):
    def test_readme_names_every_adr(self) -> None:
        adrs = sorted(ADR_DIR.glob("*.md"))
        self.assertTrue(adrs, "the ADR directory is empty")
        numbers = [int(path.stem[:4]) for path in adrs]
        self.assertEqual(numbers, list(range(1, len(adrs) + 1)),
                         "ADR numbering must be contiguous")
        readme = README.read_text(encoding="utf-8")
        expected = (
            f"{len(adrs)} Architecture Decision Record "
            f"(0001–{numbers[-1]:04d})"
        )
        # Both the tree diagram and the documentation table carry the claim.
        self.assertEqual(readme.count(expected), 2, expected)


class EvalCountTests(unittest.TestCase):
    def test_documented_eval_counts_match_the_eval(self) -> None:
        from evals import run_agentic_eval as eval_module

        # Markdown wraps the claim across lines; compare on normalized
        # whitespace so the contract is the number, not the line break.
        readme = re.sub(r"\s+", " ", README.read_text(encoding="utf-8"))
        runbook = re.sub(
            r"\s+", " ",
            (ROOT / "Docs" / "RUNBOOK.md").read_text(encoding="utf-8"),
        )
        self.assertIn(f"{len(eval_module._ALL_SCENARIOS)} nhóm scenario", readme)
        self.assertIn(f"{len(eval_module._ALL_SCENARIOS)} nhóm", runbook)
        if not (eval_module._a2a_eval_ok and eval_module._obs_eval_ok):
            # The exact case/scenario totals include the optional-dependency
            # cases; the docs document that split instead of claiming a
            # number the current environment cannot reproduce.
            self.assertIn("case phụ thuộc tuỳ chọn", readme)
            self.assertIn("case phụ thuộc tuỳ chọn", runbook)
            return
        results = [
            result
            for scenario in eval_module._ALL_SCENARIOS
            for result in scenario()
        ]
        scenarios = len({result.scenario for result in results})
        self.assertIn(f"{scenarios} scenario/{len(results)} case", readme)
        self.assertIn(f"{scenarios} scenario/{len(results)} case", runbook)


class DocumentedCountTests(unittest.TestCase):
    def test_documented_counts_match_the_tree_and_each_other(self) -> None:
        sources = [
            path
            for path in (ROOT / "src" / "ai_assistant").rglob("*.py")
            if "__pycache__" not in path.parts
        ]
        scorecard = SCORECARD.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f"{len(sources)} files", scorecard)
        # The current quality-gate table and the follow-up section are the
        # source of truth; the dated sections below keep their own historical
        # measurements and must not be compared against today's run.
        current = scorecard.split("## Agentic capabilities", 1)[0]
        for claim in ("10 468", "2 848", "2 485", "260/260"):
            self.assertIn(claim, current)
            self.assertIn(claim, readme)
        for claim in ("10 468", "2 848", "2 485"):
            self.assertIn(claim, pyproject)
        workflow = (ROOT.parent / ".github" / "workflows" / "agent-evals.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("--min-accuracy 0.9", workflow)
        self.assertIn("--min-slice-accuracy 0.75", workflow)


if __name__ == "__main__":
    unittest.main()
