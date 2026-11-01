"""Edge coverage for both pending-action store implementations.

``PendingActionStore`` (the file-backed adapter) is dict-compatible and
fail-open by design: a corrupt or missing file loads as empty, and an
unwritable path saves as a no-op — both with a logged warning, because the
API still answers ``pending_approval`` afterwards and a silently dropped
approval is invisible (review finding 25). The uncovered arms are the rest
of the mapping protocol (delete, iterate), the successful-load merge that
keeps only mapping values, and the logged save failure when the filesystem
refuses.

``MemoryPendingStore`` is the agent layer's in-memory fallback behind the
same ``PendingActionPort`` contract (ADR 0003): its no-op load/save and the
shared expiry rule (``domain.pending.prune_expired``) are pinned here too,
so the coverage gate holds for the whole pending-store surface.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_assistant.adapters.persistence import PendingActionStore
from ai_assistant.agents.pending_store import MemoryPendingStore


class MappingProtocolTests(unittest.TestCase):
    def test_delete_removes_an_entry(self):
        store = PendingActionStore("unused.json")
        store["a"] = {"v": 1}
        del store["a"]
        self.assertNotIn("a", store)
        self.assertEqual(len(store), 0)

    def test_iteration_lists_live_keys(self):
        store = PendingActionStore("unused.json")
        store["a"] = {"v": 1}
        store["b"] = {"v": 2}
        self.assertEqual(sorted(store), ["a", "b"])
        store.pop("a")
        self.assertEqual(list(store), ["b"])


class LoadTests(unittest.TestCase):
    def test_a_mapping_payload_merges_only_mapping_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            path.write_text(json.dumps({"a": {"x": 1}, "b": "not-a-mapping"}), encoding="utf-8")
            store = PendingActionStore(path)
            store.load()
            self.assertEqual(store["a"], {"x": 1})
            self.assertNotIn("b", store)

    def test_a_missing_file_loads_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PendingActionStore(Path(directory) / "absent.json")
            store.load()
            self.assertEqual(len(store), 0)

    def test_a_corrupt_file_loads_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            path.write_text("{not json", encoding="utf-8")
            store = PendingActionStore(path)
            with self.assertLogs("ai_assistant.persistence.pending_store", "WARNING") as logged:
                store.load()
            self.assertEqual(len(store), 0)
            self.assertIn("could not be read", logged.output[0])


class SaveTests(unittest.TestCase):
    def test_a_save_round_trip_preserves_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            store = PendingActionStore(path)
            store["a"] = {"x": 1}
            store.save()
            fresh = PendingActionStore(path)
            fresh.load()
            self.assertEqual(fresh["a"], {"x": 1})

    def test_an_unwritable_path_logs_and_survives(self):
        """A file standing where a directory should be must not raise — and
        must not disappear from the log either."""
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            store = PendingActionStore(blocker / "nested" / "pending.json")
            store["a"] = {"x": 1}
            with self.assertLogs("ai_assistant.persistence.pending_store", "WARNING") as logged:
                store.save()
            self.assertEqual(store["a"], {"x": 1})
            self.assertIn("could not be written", logged.output[0])


class MemoryStoreTests(unittest.TestCase):
    """The in-memory port fallback keeps the dict semantics, drops the IO."""

    def test_set_get_and_pop_round_trip(self):
        store = MemoryPendingStore()
        store["a"] = {"x": 1}
        self.assertEqual(store.get("a"), {"x": 1})
        self.assertIsNone(store.get("missing"))
        self.assertEqual(store.pop("a"), {"x": 1})
        self.assertIsNone(store.pop("a"))

    def test_clear_empties_the_store(self):
        store = MemoryPendingStore()
        store["a"] = {"x": 1}
        store.clear()
        self.assertIsNone(store.get("a"))

    def test_load_and_save_are_no_ops_behind_an_empty_path(self):
        store = MemoryPendingStore()
        store["a"] = {"x": 1}
        store.load()
        store.save()
        self.assertEqual(store.get("a"), {"x": 1})
        self.assertEqual(store.path, "")

    def test_cleanup_expires_only_entries_older_than_the_cutoff(self):
        store = MemoryPendingStore()
        store["stamped-old"] = {"created_at": 100.0}
        store["stamped-new"] = {"created_at": 900.0}
        store["unstamped"] = {"tool": "write_file"}
        self.assertTrue(store.cleanup(500.0))
        self.assertIsNone(store.get("stamped-old"))
        self.assertEqual(store.get("stamped-new"), {"created_at": 900.0})
        self.assertEqual(store.get("unstamped"), {"tool": "write_file"})

    def test_cleanup_reports_when_nothing_expired(self):
        store = MemoryPendingStore()
        store["fresh"] = {"created_at": 900.0}
        self.assertFalse(store.cleanup(500.0))
        self.assertEqual(store.get("fresh"), {"created_at": 900.0})


if __name__ == "__main__":
    unittest.main()
