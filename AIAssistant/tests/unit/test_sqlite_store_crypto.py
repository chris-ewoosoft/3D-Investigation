"""SQLite task state is encrypted before it reaches the database file.

These cases exercise the injected cipher, the one-time plaintext migration and
the fail-closed read path when the key is unavailable or wrong.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai_assistant.adapters.persistence.sqlite_store import SqliteTaskStore
from ai_assistant.adapters.persistence.storage_crypto import (
    AesGcmFieldCipher,
    StorageDecryptionError,
    StorageKeyError,
    is_sealed,
)
from ai_assistant.domain.tasks import AgentTask, TaskStatus

_KEY = b"a" * 32


def _task(**overrides) -> AgentTask:
    fields = {"capability": "chat", "message": "sensitive prompt"}
    fields.update(overrides)
    return AgentTask.new(**fields)


def _cipher() -> AesGcmFieldCipher:
    return AesGcmFieldCipher(_KEY)


class EncryptedSqliteTaskStoreTests(unittest.TestCase):
    def test_a_sealed_value_cannot_be_moved_to_another_row(self):
        """The associated data binds the value to the **row**, not the field.

        The context used to be the field name alone, so a ciphertext copied
        from one task to another opened perfectly and was then served as the
        second task's content — the store authenticated the *column* and not
        the *row*.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                first = _task(message="SECRET-A")
                second = _task(message="SECRET-B")
                store.create(first)
                store.create(second)
            finally:
                store.close()

            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE tasks SET message=(SELECT message FROM tasks WHERE id=?) WHERE id=?",
                    (second.id, first.id),
                )
                connection.commit()
            finally:
                connection.close()

            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                with self.assertRaises(StorageDecryptionError):
                    store.get(first.id)
            finally:
                store.close()

    def test_the_row_identity_cannot_be_forged(self):
        """``owner``/``classification``/``status`` decide who may read a row.

        They are plain text in the file, so an attacker with write access
        could move a task to another tenant, downgrade its classification or
        mark it completed. The sealed identity beside them is what makes the
        columns trustworthy — a row whose columns no longer match its sealed
        identity is refused.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task()
                store.create(task)
            finally:
                store.close()

            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE tasks SET owner=?, classification=?, status=? WHERE id=?",
                    ("attacker", "public", "completed", task.id),
                )
                connection.commit()
            finally:
                connection.close()

            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                with self.assertRaises(StorageDecryptionError):
                    store.get(task.id)
            finally:
                store.close()

    def test_a_state_file_that_predates_the_row_binding_is_refused(self):
        """A store with a key never accepts content it cannot authenticate.

        The file that predates the row-bound sealing holds values that are
        bound to the field name alone: the store cannot tell a moved value
        from a legitimate one, so it refuses the file and names the operator
        action instead of migrating it implicitly (ADR 0036's rule).
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                store.create(_task())
            finally:
                store.close()

            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(StorageDecryptionError) as caught:
                SqliteTaskStore(path, cipher=_cipher())
            self.assertIn("migrate_state_row_binding.py", str(caught.exception))

    def test_a_row_without_an_identity_is_refused(self):
        """A row that carries no identity was never written by this platform.

        An attacker who can write the state file can null the column; the
        store must not fall back to "no identity, no check" — that would be
        exactly the hole the identity closes.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task()
                store.create(task)
            finally:
                store.close()

            connection = sqlite3.connect(path)
            try:
                connection.execute("UPDATE tasks SET row_identity=NULL WHERE id=?", (task.id,))
                connection.commit()
            finally:
                connection.close()

            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                with self.assertRaises(StorageDecryptionError):
                    store.get(task.id)
            finally:
                store.close()

    def test_task_and_event_content_is_sealed_on_disk_and_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task(metadata={"secret": "metadata-secret"})
                store.create(task)
                store.append_event(task.id, "status", {"secret": "event-secret"})
                updated = task.transition(TaskStatus.FAILED, error="failure-secret")
                store.save(updated)

                loaded = store.get(task.id)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.message, "sensitive prompt")
                self.assertEqual(loaded.metadata, {"secret": "metadata-secret"})
                self.assertEqual(loaded.error, "failure-secret")
                self.assertEqual([item.message for item in store.list()], ["sensitive prompt"])
                self.assertEqual(
                    [item["data"]["secret"] for item in store.events(task.id)],
                    ["event-secret"],
                )
            finally:
                store.close()

            connection = sqlite3.connect(path)
            try:
                row = connection.execute(
                    "SELECT message, error, metadata, result FROM tasks",
                ).fetchone()
                event_data = connection.execute("SELECT data FROM task_events").fetchone()[0]
            finally:
                connection.close()
            self.assertTrue(all(is_sealed(value) for value in row if value is not None))
            self.assertTrue(is_sealed(event_data))
            for value in (*row, event_data):
                self.assertNotIn("secret", value or "")

    def test_completed_result_is_sealed_and_read_back(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteTaskStore(Path(directory) / "tasks.sqlite", cipher=_cipher())
            try:
                task = _task()
                store.create(task)
                store.save(task.transition(TaskStatus.COMPLETED, result={"content": "secret-result"}))
                self.assertEqual(store.get(task.id).result, {"content": "secret-result"})
            finally:
                store.close()

    def test_cas_update_seals_the_new_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task()
                store.create(task)
                self.assertTrue(
                    store.save_if_status(
                        task.transition(TaskStatus.COMPLETED, result={"content": "cas-secret"}),
                        TaskStatus.SUBMITTED,
                    ),
                )
                self.assertEqual(store.get(task.id).result, {"content": "cas-secret"})
            finally:
                store.close()
            connection = sqlite3.connect(path)
            try:
                result = connection.execute("SELECT result FROM tasks").fetchone()[0]
            finally:
                connection.close()
            self.assertTrue(is_sealed(result))

    def test_a_plaintext_store_is_refused_when_a_key_is_configured(self):
        """The store never migrates implicitly (ADR 0036).

        The signal that said *"this file has been sealed"* lived **inside**
        the file, so anyone with write access could forge it — and a forged
        version made the migration re-seal the attacker's own substituted
        rows. A legacy file and a substituted file are the same bytes, so
        the application cannot decide safely; only the operator can, by
        running the explicit migration.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            plain = SqliteTaskStore(path)
            try:
                task = _task()
                plain.create(task)
                plain.append_event(task.id, "status", {"secret": "legacy-event"})
            finally:
                plain.close()

            with self.assertRaises(StorageDecryptionError) as raised:
                SqliteTaskStore(path, cipher=_cipher())
            self.assertIn("seal_plaintext_state.py", str(raised.exception))

    def test_a_forged_schema_version_cannot_reopen_a_plaintext_store(self):
        """The attack ADR 0036 closed: forge the version, get the migration.

        Forging ``PRAGMA user_version`` used to make the store run its
        one-time migration, which sealed the *attacker's* substituted rows
        and then served them as if the platform had written them.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            plain = SqliteTaskStore(path)
            try:
                task = _task()
                plain.create(task)
            finally:
                plain.close()

            connection = sqlite3.connect(path)
            try:
                connection.execute("UPDATE tasks SET message = ?", ("substituted",))
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(StorageDecryptionError):
                SqliteTaskStore(path, cipher=_cipher())

    def test_the_explicit_migration_seals_a_plaintext_store(self):
        """The one-time migration is an operator action, not an open-time one.

        After it, the store opens with the key and the content reads back
        unchanged — and the version is marked, so the store does not
        migrate again.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            plain = SqliteTaskStore(path)
            try:
                task = _task()
                plain.create(task)
                plain.append_event(task.id, "status", {"secret": "legacy-event"})
            finally:
                plain.close()

            cipher = _cipher()
            sealed = SqliteTaskStore(path)
            try:
                self.assertEqual(sealed.seal_plaintext_content(cipher), 2)
            finally:
                sealed.close()

            encrypted = SqliteTaskStore(path, cipher=cipher)
            try:
                self.assertEqual(
                    encrypted._conn.execute("PRAGMA user_version").fetchone()[0], 4,
                )
                self.assertEqual(encrypted.get(task.id).message, "sensitive prompt")
                self.assertEqual(encrypted.events(task.id).__next__()["data"]["secret"], "legacy-event")
            finally:
                encrypted.close()

            connection = sqlite3.connect(path)
            try:
                message = connection.execute("SELECT message FROM tasks").fetchone()[0]
                event_data = connection.execute("SELECT data FROM task_events").fetchone()[0]
            finally:
                connection.close()
            self.assertTrue(is_sealed(message))
            self.assertTrue(is_sealed(event_data))

            # A second open sees the row-bound version and does not migrate again.
            reopened = SqliteTaskStore(path, cipher=cipher)
            try:
                self.assertEqual(reopened.get(task.id).message, "sensitive prompt")
            finally:
                reopened.close()

    def test_the_sealing_refuses_a_state_that_is_already_sealed(self):
        """Sealing is for *plaintext* state; a sealed state belongs to the
        row-binding migration.

        Re-sealing half-way — the identity written, the content left in the
        old format — would leave a file that claims the new version and
        cannot be read. The action therefore refuses and names the right one.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            plain = SqliteTaskStore(path)
            try:
                task = _task()
                plain.create(task)
            finally:
                plain.close()

            cipher = _cipher()
            sealed = SqliteTaskStore(path)
            try:
                self.assertEqual(sealed.seal_plaintext_content(cipher), 1)
                with self.assertRaises(StorageDecryptionError) as raised:
                    sealed.seal_plaintext_content(cipher)
                self.assertIn("migrate_state_row_binding.py", str(raised.exception))
            finally:
                sealed.close()

            # The refusal left the state exactly as the first seal wrote it.
            encrypted = SqliteTaskStore(path, cipher=cipher)
            try:
                self.assertEqual(encrypted.get(task.id).message, "sensitive prompt")
            finally:
                encrypted.close()

    def test_a_substituted_event_payload_is_refused(self):
        """The event log is content too — it must be sealed as well.

        The check is over *every* content column and *every* event payload,
        not a sample: a file whose task rows happen to be sealed but whose
        event log was substituted must still be refused.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            cipher = _cipher()
            store = SqliteTaskStore(path, cipher=cipher)
            try:
                task = _task()
                store.create(task)
                store.append_event(task.id, "status", {"secret": "event-secret"})
            finally:
                store.close()

            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE task_events SET data = ? WHERE task_id = ?",
                    ('{"secret": "substituted"}', task.id),
                )
                connection.execute("PRAGMA user_version = 1")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(StorageDecryptionError):
                SqliteTaskStore(path, cipher=cipher)

    def test_the_explicit_migration_refuses_a_sealed_state(self):
        """Re-running it must not seal a sealed value twice — it refuses.

        The sealed values carry the *current* context, so sealing them again
        would be harmless in itself; what is not harmless is the version the
        action records. A file that claims the row-bound version while its
        content is bound to another context cannot be read. The action
        therefore refuses and names the row-binding migration.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            plain = SqliteTaskStore(path)
            try:
                task = _task()
                plain.create(task)
                plain.append_event(task.id, "status", {"secret": "event-secret"})
            finally:
                plain.close()
            cipher = _cipher()
            store = SqliteTaskStore(path)
            try:
                self.assertEqual(store.seal_plaintext_content(cipher), 2)
                with self.assertRaises(StorageDecryptionError) as raised:
                    store.seal_plaintext_content(cipher)
                self.assertIn("migrate_state_row_binding.py", str(raised.exception))
            finally:
                store.close()

    def test_the_explicit_migration_refuses_a_sealed_event(self):
        """An event payload that is already sealed is refused too.

        The content columns and the event log share one sealing format: a
        half-migrated file (the columns re-sealed, the event left in the old
        format) would claim the new version and fail to read.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            plain = SqliteTaskStore(path)
            try:
                task = _task()
                plain.create(task)
                plain.append_event(task.id, "status", {"secret": "event-secret"})
            finally:
                plain.close()

            cipher = _cipher()
            store = SqliteTaskStore(path)
            try:
                # Seal the columns but leave the event payload sealed in the
                # old format: the action must refuse rather than bump the
                # version over a mixed file.
                with store._conn:
                    store._conn.execute(
                        "UPDATE task_events SET data=? WHERE task_id=?",
                        (cipher.seal("legacy", context="event.data"), task.id),
                    )
                with self.assertRaises(StorageDecryptionError) as raised:
                    store.seal_plaintext_content(cipher)
                self.assertIn("migrate_state_row_binding.py", str(raised.exception))
            finally:
                store.close()

    def test_a_substituted_plaintext_column_is_refused(self):
        """An unsealed column in a migrated store is not authentic.

        The AEAD exists to reject a value the platform did not write.
        Accepting an unsealed value let anyone with write access to the file
        replace a sealed column with plaintext JSON and have it served as if
        the platform had written it — the GCM tag never got the chance to
        reject it. The binary artifacts already refuse an unsealed file for
        exactly this reason; the text columns now behave the same way.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task()
                store.create(task)
                store.append_event(task.id, "status", {"secret": "event-secret"})
            finally:
                store.close()

            # Substitute the sealed columns, as a process with write access
            # to the state file would.
            connection = sqlite3.connect(path)
            try:
                connection.execute("UPDATE tasks SET message = ? WHERE id = ?", ("substituted", task.id))
                connection.execute(
                    "UPDATE task_events SET data = ? WHERE task_id = ?",
                    ('{"secret": "substituted"}', task.id),
                )
                connection.commit()
            finally:
                connection.close()

            reopened = SqliteTaskStore(path, cipher=_cipher())
            try:
                with self.assertRaises(StorageDecryptionError):
                    reopened.get(task.id)
                with self.assertRaises(StorageDecryptionError):
                    list(reopened.events(task.id))
            finally:
                reopened.close()

    def test_a_store_without_a_cipher_still_serves_plaintext(self):
        """The sealed regime is a property of the *deployment*, not of the
        file: a store opened without a key is a plaintext store by design,
        and refusing its rows would break the opt-in path."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path)
            try:
                task = _task()
                store.create(task)
                self.assertEqual(store.get(task.id).message, "sensitive prompt")
            finally:
                store.close()

    def test_fail_orphaned_seals_its_error_and_status_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task()
                store.create(task)
                self.assertEqual(store.fail_orphaned("restart-secret"), 1)
                self.assertEqual(store.get(task.id).error, "restart-secret")
                self.assertEqual(
                    list(store.events(task.id))[-1]["data"]["error"], "restart-secret",
                )
            finally:
                store.close()
            connection = sqlite3.connect(path)
            try:
                error = connection.execute("SELECT error FROM tasks").fetchone()[0]
                data = connection.execute("SELECT data FROM task_events").fetchone()[0]
            finally:
                connection.close()
            self.assertTrue(is_sealed(error))
            self.assertTrue(is_sealed(data))

    def test_sealed_state_cannot_be_read_without_the_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task()
                store.create(task)
            finally:
                store.close()
            blind = SqliteTaskStore(path)
            try:
                with self.assertRaises(StorageKeyError):
                    blind.get(task.id)
                with self.assertRaises(StorageKeyError):
                    list(blind.list())
            finally:
                blind.close()

    def test_a_wrong_key_or_tampered_row_fails_loudly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task()
                store.create(task)
            finally:
                store.close()

            wrong = SqliteTaskStore(path, cipher=AesGcmFieldCipher(b"b" * 32))
            try:
                with self.assertRaises(StorageDecryptionError):
                    wrong.get(task.id)
            finally:
                wrong.close()

            connection = sqlite3.connect(path)
            try:
                message = connection.execute("SELECT message FROM tasks").fetchone()[0]
                connection.execute("UPDATE tasks SET metadata = ?", (message,))
                connection.commit()
            finally:
                connection.close()
            # The message envelope was moved to the metadata column: the field
            # context differs, so authentication fails before JSON parsing.
            swapped = SqliteTaskStore(path, cipher=_cipher())
            try:
                with self.assertRaises(StorageDecryptionError):
                    swapped.get(task.id)
            finally:
                swapped.close()

    def test_empty_result_and_error_columns_stay_null(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.sqlite"
            store = SqliteTaskStore(path, cipher=_cipher())
            try:
                task = _task(metadata={})
                store.create(task)
                loaded = store.get(task.id)
                self.assertIsNone(loaded.result)
                self.assertIsNone(loaded.error)
                self.assertEqual(loaded.metadata, {})
            finally:
                store.close()
            connection = sqlite3.connect(path)
            try:
                row = connection.execute("SELECT result, error, metadata FROM tasks").fetchone()
            finally:
                connection.close()
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])
            self.assertTrue(is_sealed(row[2]))


if __name__ == "__main__":
    unittest.main()
