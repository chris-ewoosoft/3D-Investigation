"""Unit tests for the LangGraph checkpoint adapter.

Postgres/Redis checkpointer imports are hidden behind fakes; the memory and
sqlite backends are exercised for real (both are in the offline CI dependency
set), so no external services are contacted.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ai_assistant.adapters.persistence import checkpointing as cp_module
from ai_assistant.settings import AgentRuntimeSettings


class _SaverContext:
    """The shape ``PostgresSaver.from_conn_string`` really returns.

    The real factory is a ``@contextmanager``: calling it produces a generator
    context manager and only ``__enter__`` yields the saver. The old test fake
    returned a saver directly, which hid the production bug where ``setup()``
    was called on the manager.
    """

    def __init__(self, saver, events: list) -> None:
        self._saver = saver
        self._events = events

    def __enter__(self):
        self._events.append("enter")
        return self._saver

    def __exit__(self, *exc_info):
        self._events.append("exit")
        return False


def _settings(**overrides) -> AgentRuntimeSettings:
    base = dict(
        sandbox_enabled=True,
        sandbox_runtime="local",
        sandbox_image="python:3.11-slim",
        max_write_bytes=10,
        checkpoint_backend="memory",
        checkpoint_url="",
        checkpoint_path="",
        native_tool_calls=False,
    )
    base.update(overrides)
    return AgentRuntimeSettings(**base)


class BuildCheckpointerTests(unittest.TestCase):
    def _patch_settings(self, settings):
        return mock.patch.object(
            cp_module, "load_agent_runtime_settings", return_value=settings,
        )

    def test_memory_backend(self):
        with self._patch_settings(_settings()):
            handle = cp_module.build_checkpointer()
        from langgraph.checkpoint.memory import MemorySaver
        self.assertIsInstance(handle.saver, MemorySaver)
        handle.close()

    def test_postgres_requires_url(self):
        with self._patch_settings(_settings(checkpoint_backend="postgres")):
            handle = cp_module.build_checkpointer()
        from langgraph.checkpoint.memory import MemorySaver
        self.assertIsInstance(handle.saver, MemorySaver)
        handle.close()

    def test_postgres_backend_enters_the_factory_context_manager(self):
        """``from_conn_string`` is a ``@contextmanager``: the saver is what it
        yields, and the manager must stay entered until the handle is closed."""
        events: list = []

        class _Saver:
            @classmethod
            def from_conn_string(cls, url):
                events.append(("url", url))
                return _SaverContext(cls(), events)

            def setup(self):
                events.append("setup")

        fake = SimpleNamespace(PostgresSaver=_Saver)
        settings = _settings(checkpoint_backend="postgres", checkpoint_url="postgres://x")
        with self._patch_settings(settings):
            with mock.patch.dict(sys.modules, {"langgraph.checkpoint.postgres": fake}):
                handle = cp_module.build_checkpointer()
        try:
            self.assertIsInstance(handle.saver, _Saver)
            self.assertIn(("url", "postgres://x"), events)
            self.assertIn("enter", events)
            self.assertIn("setup", events)
        finally:
            handle.close()
        self.assertIn("exit", events)

    def test_redis_backend_enters_the_factory_context_manager(self):
        events: list = []

        class _Saver:
            @classmethod
            def from_conn_string(cls, url):
                events.append(("url", url))
                return _SaverContext(cls(), events)

            def setup(self):
                events.append("setup")

        fake = SimpleNamespace(RedisSaver=_Saver)
        settings = _settings(checkpoint_backend="redis", checkpoint_url="redis://x")
        with self._patch_settings(settings):
            with mock.patch.dict(sys.modules, {"langgraph.checkpoint.redis": fake}):
                handle = cp_module.build_checkpointer()
        try:
            self.assertIsInstance(handle.saver, _Saver)
            self.assertIn(("url", "redis://x"), events)
            self.assertIn("enter", events)
            self.assertIn("setup", events)
        finally:
            handle.close()
        self.assertIn("exit", events)

    def test_a_failing_setup_releases_the_factory_context_manager(self):
        """A backend that connects and then fails ``setup()`` must not leak
        the connection: the failure is what the required gate reports."""
        events: list = []

        class _Saver:
            @classmethod
            def from_conn_string(cls, url):
                return _SaverContext(cls(), events)

            def setup(self):
                msg = "schema boom"
                raise RuntimeError(msg)

        fake = SimpleNamespace(PostgresSaver=_Saver)
        settings = _settings(
            checkpoint_backend="postgres", checkpoint_url="postgres://x",
            checkpoint_required=True,
        )
        with self._patch_settings(settings):
            with mock.patch.dict(sys.modules, {"langgraph.checkpoint.postgres": fake}):
                with self.assertRaisesRegex(RuntimeError, "failed to initialize"):
                    cp_module.build_checkpointer()
        self.assertIn("enter", events)
        self.assertIn("exit", events)

    def test_a_failing_sqlite_setup_releases_the_connection(self):
        """A SQLite schema failure must not leak the raw connection: the
        raise is contained by the required gate, the handle is not."""
        class _BrokenSqliteSaver:
            def __init__(self, conn, serde=None):
                self.conn = conn

            def setup(self):
                msg = "schema boom"
                raise RuntimeError(msg)

        conn = mock.MagicMock()
        fake = SimpleNamespace(SqliteSaver=_BrokenSqliteSaver)
        settings = _settings(
            checkpoint_backend="sqlite", checkpoint_path="checkpoints.sqlite",
            checkpoint_required=True,
        )
        with self._patch_settings(settings):
            with mock.patch.dict(sys.modules, {"langgraph.checkpoint.sqlite": fake}), \
                    mock.patch.object(cp_module.sqlite3, "connect", return_value=conn):
                with self.assertRaisesRegex(RuntimeError, "failed to initialize"):
                    cp_module.build_checkpointer()
        conn.close.assert_called_once()

    def test_sqlite_backend_creates_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "checkpoints.sqlite"
            settings = _settings(checkpoint_backend="sqlite", checkpoint_path=str(db))
            with self._patch_settings(settings):
                handle = cp_module.build_checkpointer()
            try:
                self.assertTrue(db.exists())
            finally:
                # Release the sqlite connection so the temp dir can be removed
                # on Windows (open file handles block deletion).
                handle.close()

    def test_a_bare_relative_sqlite_path_needs_no_parent_directory(self):
        """A bare ``AGENT_CHECKPOINT_PATH`` must not fall back to memory.

        ``os.makedirs(os.path.dirname("checkpoints.sqlite"))`` raises on the
        empty string, and the old catch-all turned that into ``MemorySaver``.
        """
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path.cwd()
            os.chdir(tmp)
            try:
                settings = _settings(
                    checkpoint_backend="sqlite", checkpoint_path="checkpoints.sqlite",
                )
                with self._patch_settings(settings):
                    handle = cp_module.build_checkpointer()
                try:
                    self.assertTrue((Path(tmp) / "checkpoints.sqlite").exists())
                finally:
                    handle.close()
            finally:
                os.chdir(previous)

    def test_a_nested_sqlite_path_creates_its_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "checkpoints.sqlite"
            cp_module._ensure_parent_directory(str(target))
            self.assertTrue(target.parent.is_dir())

    def test_unknown_backend_falls_back_with_warning(self):
        with self._patch_settings(_settings(checkpoint_backend="bogus")):
            handle = cp_module.build_checkpointer()
        from langgraph.checkpoint.memory import MemorySaver
        self.assertIsInstance(handle.saver, MemorySaver)
        handle.close()

    def test_the_sqlite_checkpoint_store_is_sealed_with_the_platform_key(self) -> None:
        """ADR 0032: the checkpoint holds the system prompt, the user's
        messages and the tool results — the same material the task store
        seals. Leaving it in the clear made ``Cache/`` the one plaintext
        copy the at-rest story had not closed."""
        from ai_assistant.adapters.persistence.storage_crypto import AesGcmFieldCipher

        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "checkpoints.sqlite"
            settings = _settings(checkpoint_backend="sqlite", checkpoint_path=str(db))
            with self._patch_settings(settings):
                handle = cp_module.build_checkpointer(AesGcmFieldCipher(b"k" * 32))
            try:
                handle.saver.put(
                    {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}, "type": "chat"},
                    {"v": 1, "id": "chk-1", "ts": "2026-01-01T00:00:00+00:00",
                     "channel_values": {"messages": [{"role": "user", "content": "the secret prompt"}]}},
                    {"source": "input", "step": 0},
                    {},
                )
            finally:
                handle.close()
            raw = db.read_bytes()
            self.assertNotIn(b"the secret prompt", raw)
            self.assertIn(b"enc:v1:", raw)

    def test_a_checkpoint_sealed_with_another_key_cannot_be_read(self) -> None:
        """Fail closed: a checkpoint the platform key cannot open is not a
        checkpoint the platform can resume from."""
        from ai_assistant.adapters.persistence.storage_crypto import (
            AesGcmFieldCipher,
            StorageDecryptionError,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "checkpoints.sqlite"
            settings = _settings(checkpoint_backend="sqlite", checkpoint_path=str(db))
            with self._patch_settings(settings):
                handle = cp_module.build_checkpointer(AesGcmFieldCipher(b"k" * 32))
            try:
                handle.saver.put(
                    {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}, "type": "chat"},
                    {"v": 1, "id": "chk-1", "ts": "2026-01-01T00:00:00+00:00",
                     "channel_values": {"messages": [{"role": "user", "content": "the secret prompt"}]}},
                    {"source": "input", "step": 0},
                    {},
                )
            finally:
                handle.close()

            with self._patch_settings(settings):
                other = cp_module.build_checkpointer(AesGcmFieldCipher(b"j" * 32))
            try:
                with self.assertRaises(StorageDecryptionError):
                    other.saver.get_tuple(
                        {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}, "type": "chat"}
                    )
            finally:
                other.close()

    def test_without_a_key_the_checkpoint_stays_in_the_clear(self) -> None:
        """No key configured means no sealing — the behaviour is explicit
        rather than silently degrading to a broken envelope."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = Path(tmpdir) / "checkpoints.sqlite"
            settings = _settings(checkpoint_backend="sqlite", checkpoint_path=str(db))
            with self._patch_settings(settings):
                handle = cp_module.build_checkpointer()
            try:
                handle.saver.put(
                    {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}, "type": "chat"},
                    {"v": 1, "id": "chk-1", "ts": "2026-01-01T00:00:00+00:00",
                     "channel_values": {"messages": [{"role": "user", "content": "plain prompt"}]}},
                    {"source": "input", "step": 0},
                    {},
                )
            finally:
                handle.close()
            self.assertIn(b"plain prompt", db.read_bytes())

    def test_a_checkpoint_sealed_with_an_unknown_cipher_is_refused(self) -> None:
        """The adapter names the cipher it sealed with. A blob that claims a
        different one is not opened — it is refused."""
        from ai_assistant.adapters.persistence.storage_crypto import AesGcmFieldCipher

        adapter = cp_module._FieldCipherAdapter(AesGcmFieldCipher(b"k" * 32))
        with self.assertRaises(ValueError):
            adapter.decrypt("rot13", b"not really ciphertext")

    def test_the_cipher_adapter_round_trips_the_bytes(self) -> None:
        from ai_assistant.adapters.persistence.storage_crypto import AesGcmFieldCipher

        adapter = cp_module._FieldCipherAdapter(AesGcmFieldCipher(b"k" * 32))
        name, sealed = adapter.encrypt(b"payload")
        self.assertEqual(name, cp_module._CHECKPOINT_CIPHER_NAME)
        self.assertEqual(adapter.decrypt(name, sealed), b"payload")

    def test_backend_failure_falls_back_to_memory(self):
        def boom():
            msg = "connection refused"
            raise RuntimeError(msg)

        settings = _settings(checkpoint_backend="postgres", checkpoint_url="postgres://x")
        with self._patch_settings(settings):
            with mock.patch.dict(sys.modules, {"langgraph.checkpoint.postgres": SimpleNamespace(PostgresSaver=boom)}):
                handle = cp_module.build_checkpointer()
        from langgraph.checkpoint.memory import MemorySaver
        self.assertIsInstance(handle.saver, MemorySaver)
        handle.close()


class RequiredCheckpointTests(unittest.TestCase):
    """ADR 0023: with ``AGENT_CHECKPOINT_REQUIRED=1`` durability is a gate,
    not a best effort — memory or a broken backend fails startup instead."""

    def _patch_settings(self, settings):
        return mock.patch.object(
            cp_module, "load_agent_runtime_settings", return_value=settings,
        )

    def test_memory_backend_is_refused(self):
        # The message is the ops contract (RUNBOOK §4.3): it must name the
        # refusal *before* any backend is built, not the generic "not usable"
        # fallback — otherwise ops cannot tell the two failures apart.
        with self._patch_settings(_settings(checkpoint_required=True)):
            with self.assertRaisesRegex(RuntimeError, "refuses the in-memory checkpointer"):
                cp_module.build_checkpointer()

    def test_unusable_backend_is_refused_instead_of_falling_back(self):
        with self._patch_settings(
            _settings(checkpoint_backend="postgres", checkpoint_required=True)
        ):
            with self.assertRaisesRegex(RuntimeError, "is not usable"):
                cp_module.build_checkpointer()

    def test_a_broken_backend_raises_instead_of_falling_back(self):
        class _BrokenSaver:
            @classmethod
            def from_conn_string(cls, url):
                msg = "connection refused"
                raise RuntimeError(msg)

        settings = _settings(
            checkpoint_backend="postgres", checkpoint_url="postgres://x",
            checkpoint_required=True,
        )
        with self._patch_settings(settings):
            with mock.patch.dict(
                sys.modules,
                {"langgraph.checkpoint.postgres": SimpleNamespace(PostgresSaver=_BrokenSaver)},
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to initialize") as raised:
                    cp_module.build_checkpointer()
        # The cause is chained so the log still names the real failure.
        self.assertIn("connection refused", str(raised.exception.__cause__))

    def test_sqlite_satisfies_the_durability_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "checkpoints.sqlite"
            settings = _settings(
                checkpoint_backend="sqlite", checkpoint_path=str(db),
                checkpoint_required=True,
            )
            with self._patch_settings(settings):
                handle = cp_module.build_checkpointer()
            try:
                self.assertTrue(db.exists())
            finally:
                handle.close()


class CheckpointerHandleTests(unittest.TestCase):
    def test_close_is_idempotent(self) -> None:
        calls: list = []
        handle = cp_module.CheckpointerHandle(object(), lambda: calls.append("closed"))
        handle.close()
        handle.close()
        self.assertEqual(calls, ["closed"])

    def test_a_memory_handle_has_nothing_to_release(self) -> None:
        handle = cp_module.CheckpointerHandle(object())
        handle.close()  # must not raise


class CleanupOldCheckpointsTests(unittest.TestCase):
    def _patch_settings(self, settings):
        return mock.patch.object(
            cp_module, "load_agent_runtime_settings", return_value=settings,
        )

    def test_non_sqlite_backend_is_noop(self):
        with (
            self._patch_settings(_settings(checkpoint_backend="memory")),
            mock.patch.object(cp_module.os.path, "exists") as exists,
        ):
            cp_module.cleanup_old_checkpoints()  # must not raise
        # The backend gate fires before the filesystem is consulted at all.
        exists.assert_not_called()

    def test_missing_database_is_noop(self):
        with (
            self._patch_settings(
                _settings(checkpoint_backend="sqlite", checkpoint_path="nope.sqlite"),
            ),
            mock.patch.object(cp_module.os.path, "exists", return_value=False) as exists,
        ):
            cp_module.cleanup_old_checkpoints()  # must not raise
        # It asked about exactly this path, got "no", and stopped there.
        exists.assert_called_once_with("nope.sqlite")

    def test_small_database_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "small.sqlite"
            db.write_bytes(b"tiny")
            with self._patch_settings(_settings(checkpoint_backend="sqlite", checkpoint_path=str(db))):
                cp_module.cleanup_old_checkpoints(max_size_mb=50)
            self.assertEqual(db.read_bytes(), b"tiny")

    def test_oversized_database_is_vacuumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "big.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT)")
            conn.execute("CREATE TABLE writes (thread_id TEXT, checkpoint_id TEXT)")
            for i in range(150):
                conn.execute(
                    "INSERT INTO checkpoints VALUES (?, ?)",
                    (f"t{i:03d}", f"c{i:03d}"),
                )
                conn.execute(
                    "INSERT INTO writes VALUES (?, ?)",
                    (f"t{i:03d}", f"c{i:03d}"),
                )
            conn.commit()
            conn.close()
            with self._patch_settings(
                _settings(checkpoint_backend="sqlite", checkpoint_path=str(db))
            ):
                cp_module.cleanup_old_checkpoints(max_size_mb=0)
            # The VACUUM path prunes non-recent threads; the DB remains valid.
            check_conn = sqlite3.connect(db)
            try:
                remaining = check_conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
            finally:
                check_conn.close()
            self.assertEqual(remaining, 100)


if __name__ == "__main__":
    unittest.main()
