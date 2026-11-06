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
        index = readme.split("Các quyết định trọng yếu, theo thứ tự:", 1)[1].split(
            "Cài đặt trong lúc phát triển:", 1,
        )[0]
        actual_links = re.findall(
            r"\[([0-9]{4})\]\(Docs/adr/([^)]+)\)", index,
        )
        expected_links = [(path.stem[:4], path.name) for path in adrs]
        self.assertEqual(
            actual_links,
            expected_links,
            "README ADR index must link every ADR exactly once and in order",
        )


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
        from scripts import mutation_check

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
        coverage = re.search(
            r"\*\*100\.00%\*\* — ([0-9 ]+) statements, ([0-9 ]+) branches",
            current,
        )
        if coverage is None:
            self.fail("current SCORECARD coverage row is missing")
        coverage_claims = coverage.groups()

        suite = unittest.defaultTestLoader.discover(
            str(ROOT / "tests"), pattern="test_*.py",
        )
        test_claim = f"{suite.countTestCases():,}".replace(",", " ")
        mutant_count = len(mutation_check.MUTANTS)
        self.assertEqual(mutant_count, mutation_check.MIN_MUTANTS)
        mutation_claim = f"{mutant_count}/{mutant_count}"

        for claim in (*coverage_claims, test_claim, mutation_claim):
            self.assertIn(claim, current)
            self.assertIn(claim, readme)
        for claim in (*coverage_claims, test_claim):
            self.assertIn(claim, pyproject)
        self.assertIn(
            f"MIN_MUTANTS = {mutation_check.MIN_MUTANTS}", current,
        )
        workflow = (ROOT.parent / ".github" / "workflows" / "agent-evals.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("--min-accuracy 0.9", workflow)
        self.assertIn("--min-slice-accuracy 0.75", workflow)


class OperationalDocsTests(unittest.TestCase):
    def test_container_model_mount_matches_runtime_data_layout(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        expected = "/app/data/AIAssistant/Models"
        stale = "/app/AIAssistant/Models"
        self.assertIn(expected, dockerfile)
        self.assertIn(expected, readme)
        self.assertNotIn(stale, dockerfile)
        self.assertNotIn(stale, readme)

    def test_runbook_pins_http_metric_and_required_tokens(self) -> None:
        runbook = (ROOT / "Docs" / "RUNBOOK.md").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("agent_http_requests_total", runbook)
        self.assertNotIn('agent_requests_total{status', runbook)
        for name in (
            "AI_ADMIN_TOKEN",
            "AI_AGENT_TOKEN",
            "AI_A2A_TOKEN",
            "AI_A2A_OUTBOUND_TOKEN",
            "AI_MCP_TOKEN",
        ):
            self.assertIn(name, runbook)
        self.assertIn("AI_A2A_OUTBOUND_TOKEN", env_example)


if __name__ == "__main__":
    unittest.main()
