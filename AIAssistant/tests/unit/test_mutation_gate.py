"""Tests for the mutation gate itself (ADR 0032).

The gate is what proves that the load-bearing decisions of the package are
pinned by a test that *fails* when the decision is inverted. Two properties
make it trustworthy:

1. Every mutant is anchored to a source anchor that must still exist — a
   mutant whose anchor drifted is *stale*, not *passed*.
2. The number of mutants is itself a gate. Without that floor, deleting a
   mutant is indistinguishable from passing it: the suite stays green, the
   coverage floor stays at 100 %, and the guard silently stops being guarded.

The script lives outside ``src/``, so it is loaded by path.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mutation_check.py"
_ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location("ai_assistant_mutation_check", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # The dataclass decorator resolves the module's namespace through
    # ``sys.modules``, so the module has to be registered before it runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class MutationGateFloorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = _load_script()

    def test_the_declared_count_meets_the_floor(self) -> None:
        self.assertGreaterEqual(len(self.script.MUTANTS), self.script.MIN_MUTANTS)

    def test_the_floor_names_the_guard_that_disappeared(self) -> None:
        message = self.script.floor_violation(self.script.MIN_MUTANTS - 1)
        self.assertIsNotNone(message)
        self.assertIn(str(self.script.MIN_MUTANTS), str(message))
        self.assertIn(str(self.script.MIN_MUTANTS - 1), str(message))

    def test_a_count_at_or_above_the_floor_passes(self) -> None:
        self.assertIsNone(self.script.floor_violation(self.script.MIN_MUTANTS))
        self.assertIsNone(self.script.floor_violation(self.script.MIN_MUTANTS + 1))

    def test_every_mutant_anchors_to_a_line_that_still_exists(self) -> None:
        """A stale anchor means the guard moved and the mutant no longer
        proves anything — the gate reports it as stale rather than passed."""
        for mutant in self.script.MUTANTS:
            source = _ROOT / mutant.file
            self.assertTrue(source.is_file(), f"{mutant.file} must exist")
            text = source.read_text(encoding="utf-8")
            self.assertIn(
                mutant.original,
                text,
                f"{mutant.file}: the anchor of {mutant.label!r} drifted",
            )

    def test_every_mutant_declares_the_suite_that_must_fail(self) -> None:
        for mutant in self.script.MUTANTS:
            self.assertTrue(mutant.tests, f"{mutant.label!r} declares no test")
            for suite in mutant.tests:
                self.assertTrue(
                    suite.startswith("tests."), f"{suite!r} is not a test module path"
                )

    def test_every_pinned_decision_file_declares_a_mutant(self) -> None:
        """The per-file floor: a file may not lose its last mutant while the
        global count stays above ``MIN_MUTANTS`` because another file gained
        two."""
        self.assertEqual(self.script.pinned_file_violations(), [])

    def test_the_pinned_list_is_not_vacuous(self) -> None:
        """The list must name the file whose race this round closed."""
        self.assertIn(
            "src/ai_assistant/rag/embeddings.py",
            self.script.PINNED_DECISION_FILES,
        )

    def test_a_pinned_file_without_a_mutant_is_reported(self) -> None:
        kept = tuple(
            mutant for mutant in self.script.MUTANTS
            if mutant.file != "src/ai_assistant/rag/embeddings.py"
        )
        self.assertIn(
            "src/ai_assistant/rag/embeddings.py",
            self.script.pinned_file_violations(kept),
        )

    def test_the_labels_are_unique(self) -> None:
        labels = [mutant.label for mutant in self.script.MUTANTS]
        self.assertEqual(len(labels), len(set(labels)))

    def test_the_gate_drops_the_cached_bytecode_of_the_file_it_mutates(self) -> None:
        """A stale ``.pyc`` can make a mutant look survived.

        Python validates a bytecode cache by the source's mtime **in whole
        seconds** and its size. A mutation that keeps the size (``"1"`` →
        ``"0"``) and is restored within the same second therefore leaves a
        cache that still holds the *mutated* code: the next import loads the
        mutant, and a test that should have failed passes. The gate must
        leave neither mutated source nor mutated bytecode behind.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "module.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            cache = root / "__pycache__"
            cache.mkdir()
            cached = cache / "module.cpython-311.pyc"
            cached.write_bytes(b"stale")
            other = cache / "other.cpython-311.pyc"
            other.write_bytes(b"keep")

            self.script.clear_bytecode_cache(source)

            self.assertFalse(cached.exists(), "the mutated module's bytecode must be dropped")
            self.assertTrue(other.exists(), "only the mutated module's cache is dropped")

    def test_the_gate_runs_the_suite_without_writing_bytecode(self) -> None:
        """The suite must not cache the mutated module.

        The bytecode of the mutated source would outlive the mutation (and
        survive the restore), so the next import — in the gate or in the
        suite — would run code that is not in the tree.
        """
        env = self.script.subprocess_env()
        self.assertEqual(env.get("PYTHONDONTWRITEBYTECODE"), "1")
        self.assertEqual(env.get("PYTHONPATH"), str(_ROOT / "src"))

    def test_file_selection_is_exact_and_accepts_windows_or_comma_spelling(self) -> None:
        first = self.script.MUTANTS[0].file
        second = next(mutant.file for mutant in self.script.MUTANTS if mutant.file != first)
        selected = self.script.select_mutants([f"{first},{second}"])
        self.assertEqual({mutant.file for mutant in selected}, {first, second})
        windows = self.script.select_mutants([first.replace("/", "\\")])
        self.assertTrue(windows)
        self.assertTrue(all(mutant.file == first for mutant in windows))

    def test_unknown_file_selection_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown mutation file"):
            self.script.select_mutants(["src/ai_assistant/not_a_guard.py"])

    def test_partition_keeps_one_owning_suite_in_one_sandbox(self) -> None:
        indexed = list(enumerate(self.script.MUTANTS[:40], start=1))
        partitions = self.script.partition_mutants(indexed, 3)
        owners: dict[tuple[str, ...], int] = {}
        for partition_number, partition in enumerate(partitions):
            for _index, mutant in partition:
                previous = owners.setdefault(mutant.tests, partition_number)
                self.assertEqual(previous, partition_number)

    def test_sandbox_ignore_drops_generated_and_model_artifacts(self) -> None:
        ignored = self.script._sandbox_ignore(
            "unused",
            ["src", "Models", "Cache", "logs", "__pycache__", "state.sqlite-wal"],
        )
        self.assertEqual(
            ignored,
            {"Models", "Cache", "logs", "__pycache__", "state.sqlite-wal"},
        )

    def test_unmodified_baseline_failure_is_not_counted_as_a_kill(self) -> None:
        failed = SimpleNamespace(returncode=1, stdout="", stderr="baseline boom")
        with mock.patch.object(self.script.subprocess, "run", return_value=failed):
            diagnostic = self.script._baseline_suite(("tests.unit.test_example",), 5, _ROOT)
        self.assertIsNotNone(diagnostic)
        self.assertIn("baseline boom", str(diagnostic))

    def test_run_mutant_restores_the_sandbox_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "src" / "guard.py"
            target.parent.mkdir(parents=True)
            original = "ENABLED = True\n"
            target.write_text(original, encoding="utf-8")
            mutant = self.script.Mutant(
                "src/guard.py",
                "ENABLED = True",
                "ENABLED = False",
                ("tests.unit.test_guard",),
                "guard disabled",
            )
            killed = SimpleNamespace(returncode=1, stdout="", stderr="failed as expected")
            with mock.patch.object(self.script.subprocess, "run", return_value=killed):
                self.assertEqual(self.script.run_mutant(mutant, base), "KILLED")
            self.assertEqual(target.read_text(encoding="utf-8"), original)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
