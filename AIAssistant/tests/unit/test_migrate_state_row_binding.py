"""The explicit one-time re-binding of the local state to its row.

The state used to be sealed with the **field name** as the AEAD associated
data, so a ciphertext could be moved from one task to another and be served
as the second task's content, and the columns that decide who may read a row
(``owner``, ``classification``, ``status``) were plain text besides.

The store refuses a file it cannot authenticate and names the operator
action; this script *is* that action. Its contract:

* it re-seals every task column and event payload with the row-bound context
  in **one transaction**;
* it writes the sealed identity beside the plain-text columns;
* it is idempotent (a value that already carries the new context is left
  alone);
* a value it cannot open aborts the migration and leaves the state as it was;
* it never prints key material and refuses to act without a configured key.
"""
from __future__ import annotations

import base64
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ai_assistant.adapters.persistence.sqlite_store import (
    SqliteTaskStore,
    event_context,
    task_context,
)
from ai_assistant.adapters.persistence.storage_crypto import (
    AesGcmFieldCipher,
    StorageCryptoError,
    StorageDecryptionError,
)
from ai_assistant.domain.tasks import AgentTask

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_state_row_binding.py"
_KEY = b"r" * 32


def _load_script():
    spec = importlib.util.spec_from_file_location("migrate_state_row_binding", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # The dataclass decorator looks the module up in ``sys.modules``; a module
    # that is not registered there cannot be executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _key_text() -> str:
    return "base64:" + base64.urlsafe_b64encode(_KEY).decode("ascii")


def _cipher() -> AesGcmFieldCipher:
    return AesGcmFieldCipher(_KEY)


class MigrateStateRowBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "tasks.sqlite"
        self.module = _load_script()
        self.module.get_paths = lambda: SimpleNamespace(data_dir=self.root)
        self._env = mock.patch.dict("os.environ", {"AI_STORAGE_KEY": _key_text()}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def _row_bound_state(self) -> AgentTask:
        store = SqliteTaskStore(self.db_path, cipher=_cipher())
        try:
            task = AgentTask.new(capability="chat", message="legacy prompt")
            store.create(task)
            store.append_event(task.id, "status", {"secret": "legacy-event"})
        finally:
            store.close()
        return task

    def _downgrade_to_the_legacy_format(self) -> None:
        """Rewrite the file the way the previous format wrote it."""
        cipher = _cipher()
        connection = sqlite3.connect(str(self.db_path))
        try:
            rows = connection.execute(
                "SELECT id, message, result, error, metadata FROM tasks",
            ).fetchall()
            for task_id, message, result, error, metadata in rows:
                legacy = []
                for value, field in zip(
                    (message, result, error, metadata), ("message", "result", "error", "metadata"),
                ):
                    if value is None:
                        legacy.append(None)
                        continue
                    plaintext = cipher.open(value, context=task_context(task_id, field))
                    legacy.append(cipher.seal(plaintext, context=f"task.{field}"))
                connection.execute(
                    "UPDATE tasks SET message=?, result=?, error=?, metadata=?, row_identity=NULL "
                    "WHERE id=?",
                    (*legacy, task_id),
                )
            events = connection.execute("SELECT rowid, task_id, data FROM task_events").fetchall()
            for rowid, task_id, data in events:
                plaintext = cipher.open(data, context=event_context(task_id))
                connection.execute(
                    "UPDATE task_events SET data=? WHERE rowid=?",
                    (cipher.seal(plaintext, context="event.data"), rowid),
                )
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        finally:
            connection.close()

    def test_the_store_refuses_a_state_that_predates_the_row_binding(self) -> None:
        self._row_bound_state()
        self._downgrade_to_the_legacy_format()
        with self.assertRaises(StorageDecryptionError) as raised:
            SqliteTaskStore(self.db_path, cipher=_cipher())
        self.assertIn("migrate_state_row_binding.py", str(raised.exception))

    def test_the_script_rebinds_the_state_and_the_store_reads_it_again(self) -> None:
        task = self._row_bound_state()
        self._downgrade_to_the_legacy_format()

        output = StringIO()
        with redirect_stdout(output):
            code = self.module.main([])
        self.assertEqual(code, 0)
        self.assertIn("REBIND OK", output.getvalue())

        store = SqliteTaskStore(self.db_path, cipher=_cipher())
        try:
            loaded = store.get(task.id)
            self.assertEqual(loaded.message, "legacy prompt")
            self.assertEqual(store.events(task.id).__next__()["data"]["secret"], "legacy-event")
        finally:
            store.close()

    def test_the_script_is_idempotent(self) -> None:
        task = self._row_bound_state()
        self._downgrade_to_the_legacy_format()
        with redirect_stdout(StringIO()):
            self.module.main([])
        with redirect_stdout(StringIO()):
            self.module.main([])
        store = SqliteTaskStore(self.db_path, cipher=_cipher())
        try:
            self.assertEqual(store.get(task.id).message, "legacy prompt")
        finally:
            store.close()

    def test_verify_reports_the_format_and_changes_nothing(self) -> None:
        self._row_bound_state()
        self._downgrade_to_the_legacy_format()
        output = StringIO()
        with redirect_stdout(output):
            code = self.module.main(["--verify"])
        self.assertEqual(code, 1)
        self.assertIn("NOT ROW-BOUND", output.getvalue())
        connection = sqlite3.connect(str(self.db_path))
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
        finally:
            connection.close()

    def test_the_script_refuses_without_a_configured_key(self) -> None:
        self._row_bound_state()
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(StorageCryptoError):
                self.module.main([])

    def test_a_value_that_cannot_be_opened_aborts_the_migration(self) -> None:
        """One unreadable value leaves the state exactly as it was."""
        task = self._row_bound_state()
        self._downgrade_to_the_legacy_format()
        connection = sqlite3.connect(str(self.db_path))
        try:
            connection.execute(
                "UPDATE tasks SET message=? WHERE id=?",
                # Sealed with a context the store never used: neither the
                # legacy one nor the row-bound one can open it.
                (_cipher().seal("planted", context="task.unknown"), task.id),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(StorageCryptoError):
            with redirect_stdout(StringIO()):
                self.module.main([])
        connection = sqlite3.connect(str(self.db_path))
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(
                connection.execute("SELECT row_identity FROM tasks").fetchone()[0], None,
            )
        finally:
            connection.close()
