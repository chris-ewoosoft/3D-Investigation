"""Tests for LangGraph checkpoint backend selection and recovery.

Covers SQLite, Memory, Postgres, Redis backend selection and the safe
fallback mechanism when a backend is unavailable.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_assistant.adapters.persistence.checkpointing import (
    _resolve_checkpoint_path,
    build_checkpointer,
    cleanup_old_checkpoints,
)


class CheckpointBackendTests(unittest.TestCase):
    """Test checkpointer backend selection logic."""

    def test_memory_backend_when_no_config(self) -> None:
        """Default to MemorySaver when no backend is configured."""
        with patch.dict(os.environ, {"AGENT_CHECKPOINT_BACKEND": "memory"}):
            handle = build_checkpointer()
            self.assertIsNotNone(handle.saver)
            handle.close()

    def test_unknown_backend_falls_back_to_memory(self) -> None:
        """Unknown backend falls back to MemorySaver with warning."""
        with patch.dict(os.environ, {"AGENT_CHECKPOINT_BACKEND": "unknown"}):
            handle = build_checkpointer()
            self.assertIsNotNone(handle.saver)
            handle.close()

    def test_postgres_backend_without_url_falls_back(self) -> None:
        """Postgres backend without URL falls back to MemorySaver."""
        with patch.dict(os.environ, {
            "AGENT_CHECKPOINT_BACKEND": "postgres",
            "AGENT_CHECKPOINT_URL": "",
        }):
            handle = build_checkpointer()
            self.assertIsNotNone(handle.saver)
            handle.close()

    def test_redis_backend_without_url_falls_back(self) -> None:
        """Redis backend without URL falls back to MemorySaver."""
        with patch.dict(os.environ, {
            "AGENT_CHECKPOINT_BACKEND": "redis",
            "AGENT_CHECKPOINT_URL": "",
        }):
            handle = build_checkpointer()
            self.assertIsNotNone(handle.saver)
            handle.close()

    def test_backend_import_failure_falls_back_to_memory(self) -> None:
        """Import failure for optional backend falls back to MemorySaver."""
        with patch.dict(os.environ, {
            "AGENT_CHECKPOINT_BACKEND": "postgres",
            "AGENT_CHECKPOINT_URL": "postgresql://fake",
        }):
            handle = build_checkpointer()
            self.assertIsNotNone(handle.saver)
            handle.close()


class CheckpointCleanupTests(unittest.TestCase):
    """Test checkpoint cleanup and vacuum operations."""

    def test_cleanup_noop_for_non_sqlite(self) -> None:
        """Cleanup is a no-op for non-SQLite backends."""
        with (
            patch.dict(os.environ, {"AGENT_CHECKPOINT_BACKEND": "memory"}),
            patch("ai_assistant.adapters.persistence.checkpointing.os.path.exists") as exists,
        ):
            cleanup_old_checkpoints()
        # The backend gate fires before the filesystem is consulted at all.
        exists.assert_not_called()

    def test_cleanup_handles_missing_database(self) -> None:
        """Cleanup handles missing database file gracefully."""
        missing = "/nonexistent/path/checkpoints.sqlite"
        with (
            patch.dict(os.environ, {
                "AGENT_CHECKPOINT_BACKEND": "sqlite",
                "AGENT_CHECKPOINT_PATH": missing,
            }),
            patch(
                "ai_assistant.adapters.persistence.checkpointing.os.path.exists",
                return_value=False,
            ) as exists,
        ):
            cleanup_old_checkpoints()
        # It asked about exactly this path, got "no", and stopped there.
        exists.assert_called_once_with(missing)

    def test_checkpoint_path_resolution_covers_defaults_and_legacy_extensions(self) -> None:
        self.assertEqual(_resolve_checkpoint_path(""), "Cache/checkpoint.sql")

        with tempfile.TemporaryDirectory(dir=".") as directory:
            root = Path(directory)
            relative_root = Path(root.name)

            sql_path = relative_root / "from-sql.sql"
            sql_path.write_text("", encoding="utf-8")
            self.assertEqual(
                Path(_resolve_checkpoint_path(str(sql_path.with_suffix(".sqlite")))),
                sql_path,
            )

            sqlite_path = relative_root / "from-sqlite.sqlite"
            sqlite_path.write_text("", encoding="utf-8")
            self.assertEqual(
                Path(_resolve_checkpoint_path(str(sqlite_path.with_suffix(".sql")))),
                sqlite_path,
            )

            existing = relative_root / "existing.sql"
            existing.write_text("", encoding="utf-8")
            self.assertEqual(Path(_resolve_checkpoint_path(str(existing))), existing)

            if Path.cwd().name == "AIAssistant":
                duplicated = Path("AIAssistant") / relative_root / "existing.sql"
                self.assertEqual(Path(_resolve_checkpoint_path(str(duplicated))), existing)


if __name__ == "__main__":
    unittest.main()
