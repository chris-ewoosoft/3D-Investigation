"""Guard-path coverage for checkpoint hygiene and LangSmith compatibility.

``cleanup_old_checkpoints`` must never break startup: a corrupt database logs
a warning instead of raising. ``_vacuum_database`` always closes its handle —
including when the prune itself fails or the file was never a database — so
no lock survives on Windows. The LangSmith shims translate between SDK
generations (``id`` vs ``run_id``); when both spellings fail they return to
silence rather than failing the run they observe.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import ai_assistant.observability.langsmith_integration as langsmith_mod
from ai_assistant.adapters.persistence.checkpointing import (
    _vacuum_database,
    cleanup_old_checkpoints,
)


class CleanupOldCheckpointsTests(unittest.TestCase):
    def _settings(self, path: str):
        return SimpleNamespace(checkpoint_backend="sqlite", checkpoint_path=path)

    def test_a_corrupt_database_logs_a_warning_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            garbage = Path(directory) / "checkpoints.db"
            garbage.write_bytes(b"not a sqlite database" * 100_000)
            with (
                patch(
                    "ai_assistant.adapters.persistence.checkpointing"
                    ".load_agent_runtime_settings",
                    return_value=self._settings(str(garbage)),
                ),
                self.assertLogs(
                    "ai_assistant.adapters.persistence.checkpointing", level="WARNING",
                ) as logged,
            ):
                cleanup_old_checkpoints(max_size_mb=0)
            self.assertTrue(
                any("Could not clean up checkpoints" in line for line in logged.output),
                logged.output,
            )

    def test_a_missing_database_is_a_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent.db"
            with (
                patch(
                    "ai_assistant.adapters.persistence.checkpointing"
                    ".load_agent_runtime_settings",
                    return_value=self._settings(str(missing)),
                ),
                patch(
                    "ai_assistant.adapters.persistence.checkpointing.os.path.exists",
                    return_value=False,
                ) as exists,
            ):
                cleanup_old_checkpoints()
            # It asked about exactly this path, got "no", and stopped there …
            exists.assert_called_once_with(str(missing))
            # … and no placeholder database appeared either.
            self.assertFalse(missing.exists())

    def test_a_non_sqlite_backend_skips_cleanup(self):
        with (
            patch(
                "ai_assistant.adapters.persistence.checkpointing"
                ".load_agent_runtime_settings",
                return_value=SimpleNamespace(checkpoint_backend="memory", checkpoint_path="x"),
            ),
            patch("ai_assistant.adapters.persistence.checkpointing.os.path.exists") as exists,
        ):
            cleanup_old_checkpoints()
        # The backend gate fires before the filesystem is consulted at all.
        exists.assert_not_called()


class VacuumDatabaseTests(unittest.TestCase):
    def test_a_missing_schema_is_rolled_back_before_vacuum(self):
        """A database without checkpoint tables fails the prune, rolls back,
        and still compacts when the size cap fired. ``:memory:`` keeps the test
        off the filesystem entirely (no Windows file locks to clean up)."""
        import sqlite3

        real = sqlite3.connect(":memory:")
        try:
            spy = MagicMock(wraps=real)
            with patch(
                "ai_assistant.adapters.persistence.checkpointing.sqlite3.connect",
                return_value=spy,
            ):
                _vacuum_database(
                    ":memory:",
                    cutoff_unix=0,
                    prune_to_recent_threads=True,
                )
            # The retention query really ran and hit the missing table …
            self.assertTrue(
                any("SELECT thread_id" in str(call) for call in spy.execute.call_args_list),
            )
            # … the failed transaction was rolled back …
            spy.rollback.assert_called_once()
            # … compaction still happened …
            spy.execute.assert_any_call("VACUUM")
            # … and the handle was released rather than left holding a lock.
            spy.close.assert_called_once()
        finally:
            real.close()

    def test_an_unopenable_file_closes_nothing_and_raises(self):
        with self.assertRaises(Exception):
            _vacuum_database(
                str(Path(tempfile.gettempdir()) / "no-such-dir-xyz" / "c.db"),
                cutoff_unix=0,
            )


class LangsmithCompatibilityTests(unittest.TestCase):
    def setUp(self):
        langsmith_mod._active_run_trees.clear()
        langsmith_mod._active_run_tokens.clear()
        self.addCleanup(langsmith_mod._active_run_trees.clear)
        self.addCleanup(langsmith_mod._active_run_tokens.clear)

    @staticmethod
    def _sdk_modules(context, run_tree):
        internal = ModuleType("langsmith._internal")
        internal._context = context
        trees = ModuleType("langsmith.run_trees")
        trees.RunTree = run_tree
        return {"langsmith._internal": internal, "langsmith.run_trees": trees}

    def test_start_returns_none_when_both_signatures_fail(self):
        client = MagicMock()
        client.create_run.side_effect = [TypeError("unexpected id"), RuntimeError("down")]
        with patch.object(langsmith_mod, "_get_client", return_value=client):
            self.assertIsNone(langsmith_mod.start_langsmith_run("n", "chain", {}))
        self.assertEqual(client.create_run.call_count, 2)

    def test_start_succeeds_on_the_legacy_signature(self):
        """An old client takes the retry path and still returns the run id."""
        client = MagicMock()
        client.create_run.side_effect = [TypeError("unexpected id"), None]
        with patch.object(langsmith_mod, "_get_client", return_value=client):
            self.assertIsNotNone(langsmith_mod.start_langsmith_run("n", "chain", {}))

    def test_start_without_a_client_returns_none(self):
        with patch.object(langsmith_mod, "_get_client", return_value=None):
            self.assertIsNone(langsmith_mod.start_langsmith_run("n", "chain", {}))

    def test_finish_ignores_a_client_that_rejects_both_signatures(self):
        client = MagicMock()
        client.update_run.side_effect = [TypeError("unexpected run_id"), RuntimeError("down")]
        with patch.object(langsmith_mod, "_get_client", return_value=client):
            langsmith_mod.finish_langsmith_run("run-1", {"ok": True})
        self.assertEqual(client.update_run.call_count, 2)

    def test_finish_without_a_client_is_a_noop(self):
        with patch.object(langsmith_mod, "_get_client", return_value=None) as get_client:
            self.assertIsNone(langsmith_mod.finish_langsmith_run("run-1", {}))
        # The guard is the client lookup itself: no client, no SDK call.
        get_client.assert_called_once()

    def test_run_tree_context_is_installed_and_reset_for_nested_sdk_traces(self):
        client = MagicMock()
        token = object()
        parent = object()
        reference = MagicMock()
        reference.set.return_value = token
        context = SimpleNamespace(
            get_current_run_tree=lambda: parent,
            _PARENT_RUN_TREE_REF=reference,
        )
        created: list[object] = []

        class Tree:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        def run_tree(**kwargs):
            tree = Tree(**kwargs)
            created.append(tree)
            return tree

        modules = self._sdk_modules(context, run_tree)
        with patch.dict(sys.modules, modules):
            with patch.object(langsmith_mod, "_get_client", return_value=client):
                run_id = langsmith_mod.start_langsmith_run(
                    "agent.plan", "chain", {"node": "plan"}, {"iteration": 1},
                )
                self.assertIsNotNone(run_id)
                self.assertIs(created[0].ls_client, client)
                self.assertIs(created[0].parent_run, parent)
                self.assertIs(langsmith_mod._active_run_trees[run_id], created[0])
                self.assertIs(langsmith_mod._active_run_tokens[run_id], token)
                langsmith_mod.finish_langsmith_run(run_id, {"plan": ["inspect"]})

        reference.reset.assert_called_once_with(token)
        self.assertEqual(langsmith_mod._active_run_trees, {})
        self.assertEqual(langsmith_mod._active_run_tokens, {})

    def test_run_tree_context_failure_does_not_drop_the_manual_run(self):
        client = MagicMock()
        context = SimpleNamespace(
            get_current_run_tree=MagicMock(side_effect=RuntimeError("context unavailable")),
            _PARENT_RUN_TREE_REF=MagicMock(),
        )
        modules = self._sdk_modules(context, MagicMock())
        with patch.dict(sys.modules, modules):
            with patch.object(langsmith_mod, "_get_client", return_value=client):
                run_id = langsmith_mod.start_langsmith_run("agent.reason", "chain", {})
        self.assertIsNotNone(run_id)
        self.assertNotIn(run_id, langsmith_mod._active_run_trees)
        self.assertNotIn(run_id, langsmith_mod._active_run_tokens)

    def test_run_tree_reset_failure_is_contained_and_state_is_released(self):
        run_id = "run-with-broken-token"
        langsmith_mod._active_run_trees[run_id] = object()
        langsmith_mod._active_run_tokens[run_id] = object()
        reference = MagicMock()
        reference.reset.side_effect = RuntimeError("wrong context")
        context = SimpleNamespace(_PARENT_RUN_TREE_REF=reference)
        modules = self._sdk_modules(context, MagicMock())
        with patch.dict(sys.modules, modules):
            with patch.object(langsmith_mod, "_get_client", return_value=None):
                langsmith_mod.finish_langsmith_run(run_id, {})
        self.assertEqual(langsmith_mod._active_run_trees, {})
        self.assertEqual(langsmith_mod._active_run_tokens, {})


if __name__ == "__main__":
    unittest.main()
