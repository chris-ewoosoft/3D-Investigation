"""The explicit one-time sealing of a plaintext local state (ADR 0036).

The application no longer migrates a plaintext state file on open: the signal
that said *"this file has been sealed"* lived inside the file, so anyone with
write access could forge it — and a forged version made the migration seal the
attacker's own substituted rows.

This script is the operator's one-time action instead. Its contract:

* it seals the task state and the pending payload under the **configured**
  key, in one transaction each;
* it never prints key material;
* ``--verify`` reports whether the state is sealed and changes nothing;
* it refuses to do anything when no key is configured.
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

from ai_assistant.adapters.persistence.pending_store import PendingActionStore
from ai_assistant.adapters.persistence.sqlite_store import SqliteTaskStore
from ai_assistant.adapters.persistence.storage_crypto import (
    AesGcmFieldCipher,
    StorageCryptoError,
    is_sealed,
)
from ai_assistant.domain.tasks import AgentTask

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seal_plaintext_state.py"
_KEY = b"k" * 32


def _load_script():
    spec = importlib.util.spec_from_file_location("seal_plaintext_state", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # The dataclass decorator looks the module up in ``sys.modules``; a module
    # that is not registered there cannot be executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _key_text() -> str:
    return "base64:" + base64.urlsafe_b64encode(_KEY).decode("ascii")


class SealPlaintextStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "tasks.sqlite"
        self.pending_path = self.root / "pending_agent_actions.json"
        self.module = _load_script()
        self.module.get_paths = lambda: SimpleNamespace(data_dir=self.root)
        self._env = mock.patch.dict("os.environ", {"AI_STORAGE_KEY": _key_text()}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def _legacy_state(self) -> AgentTask:
        store = SqliteTaskStore(self.db_path)
        try:
            task = AgentTask.new(capability="chat", message="legacy prompt")
            store.create(task)
            store.append_event(task.id, "status", {"secret": "legacy-event"})
        finally:
            store.close()
        pending = PendingActionStore(self.pending_path)
        pending["act-1"] = {"tool": "write_file"}
        pending.save()
        return task

    def _raw_message(self) -> str:
        connection = sqlite3.connect(str(self.db_path))
        try:
            return str(connection.execute("SELECT message FROM tasks").fetchone()[0])
        finally:
            connection.close()

    def test_the_state_is_sealed_and_reads_back(self) -> None:
        task = self._legacy_state()
        buffer = StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(self.module.main([]), 0)
        self.assertIn("SEAL OK", buffer.getvalue())
        self.assertTrue(is_sealed(self._raw_message()))
        self.assertTrue(self.pending_path.read_text(encoding="utf-8").startswith("enc:v1:"))

        reopened = SqliteTaskStore(self.db_path, cipher=AesGcmFieldCipher(_KEY))
        try:
            self.assertEqual(reopened.get(task.id).message, "legacy prompt")
        finally:
            reopened.close()

    def test_verify_reports_the_state_and_changes_nothing(self) -> None:
        self._legacy_state()
        before = self._raw_message()
        buffer = StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(self.module.main(["--verify"]), 1)
        self.assertIn("NOT SEALED", buffer.getvalue())
        self.assertEqual(self._raw_message(), before)

        with redirect_stdout(StringIO()):
            self.assertEqual(self.module.main([]), 0)
        buffer = StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(self.module.main(["--verify"]), 0)
        self.assertIn("SEALED", buffer.getvalue())

    def test_without_a_key_the_script_refuses(self) -> None:
        """It must not invent a key: it seals with the one the app opens with."""
        self._legacy_state()
        with mock.patch.dict("os.environ", {"AI_STORAGE_KEY": ""}, clear=False):
            with self.assertRaises(StorageCryptoError):
                self.module.main([])
        self.assertFalse(is_sealed(self._raw_message()))

    def test_an_absent_state_is_a_no_op(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(self.module.main([]), 0)
        self.assertIn("no local state", buffer.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
