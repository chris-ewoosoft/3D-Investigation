"""SQLite-backed implementation of the TaskStore port."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_assistant.domain.security import DataClassification, ExecutionOrigin
from ai_assistant.domain.tasks import AgentTask, TaskStatus

from .storage_crypto import (
    FieldCipher,
    StorageDecryptionError,
    StorageKeyError,
    is_sealed,
    reseal_text,
)

#: The schema version that introduced the **row-bound** sealing. A file that
#: predates it cannot authenticate its rows: the sealed values were bound to
#: the field name alone, so a value could be moved between rows, and the
#: identity columns (owner/classification/status) were plain text. The store
#: refuses such a file and the operator runs the migration once
#: (``scripts/migrate_state_row_binding.py``) — ADR 0036's rule: a store with
#: a key never accepts content it cannot authenticate, and only the operator
#: can tell a legacy file from a substituted one.
ROW_BOUND_SCHEMA_VERSION = 4


def task_context(task_id: str, field: str) -> str:
    """The AEAD associated data of one task column.

    The **row id** is part of it. The context used to be the field name
    alone, which authenticated the *field* but not the *row*: a ciphertext
    copied from one task to another opened perfectly and was then served as
    the second task's content. Binding the row id makes that substitution
    fail authentication.
    """
    return f"task.v2:{task_id}:{field}"


def event_context(task_id: str) -> str:
    """The AEAD associated data of one task event.

    Bound to the owning task, so an event payload cannot be reparented to
    another task's stream (the ``task_id`` column is plain text).
    """
    return f"event.v2:{task_id}:data"


def _identity_bundle(task: AgentTask) -> dict[str, str]:
    """The row's identity: the plain-text columns that decide who may read it.

    ``owner`` decides the tenant that may read the row, ``classification``
    decides whether it may leave the trust boundary, and ``status`` is the
    state the platform reports. None of them was authenticated: an attacker
    with write access to the state file could move a task to another tenant,
    downgrade its classification or mark it completed.
    """
    return {
        "owner": task.owner,
        "classification": task.classification.value,
        "status": str(task.status),
        "context_id": task.context_id or "",
        "origin": task.origin.value,
    }


class SqliteTaskStore:
    """Durable task store backed by a local SQLite database.

    Thread-safety: each call acquires the module-level ``check_same_thread=False``
    connection through a re-entrant lock so multiple threads can share one store.
    When a :class:`FieldCipher` is injected, every message/metadata/result/error
    column and every event payload is sealed before it reaches the file.
    """

    def __init__(self, db_path: str | Path, *, cipher: FieldCipher | None = None) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._cipher = cipher
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._sealed_state = False
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()
        except BaseException:
            # A refused state must not leave the database file open: the
            # connection holds a lock on it (on Windows the file cannot even
            # be replaced or deleted while it is open), and a half-open
            # store would keep the deployment from repairing the state.
            self._conn.close()
            raise

    # ── Schema ─────────────────────────────────────────────────────────────────

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id              TEXT PRIMARY KEY,
                    capability      TEXT NOT NULL,
                    message         TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    result          TEXT,
                    error           TEXT,
                    metadata        TEXT NOT NULL DEFAULT '{}',
                    context_id      TEXT,
                    classification  TEXT NOT NULL DEFAULT 'internal',
                    origin          TEXT NOT NULL DEFAULT 'local',
                    row_identity    TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id     TEXT NOT NULL REFERENCES tasks(id),
                    kind        TEXT NOT NULL,
                    data        TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_context ON tasks(context_id);
                CREATE INDEX IF NOT EXISTS idx_task_created ON tasks(created_at);
                CREATE INDEX IF NOT EXISTS idx_event_task ON task_events(task_id);
            """)
            # A database written before these columns existed would otherwise
            # keep the old shape forever (``CREATE TABLE IF NOT EXISTS`` never
            # alters), and every reload would hand the domain a task whose
            # classification and origin had been silently rewritten. The
            # schema version makes the migration run exactly once instead of
            # probing column names on every startup.
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version < 1:
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(tasks)")}
                if "classification" not in columns:
                    self._conn.execute(
                        "ALTER TABLE tasks ADD COLUMN classification TEXT NOT NULL DEFAULT 'internal'",
                    )
                if "origin" not in columns:
                    self._conn.execute("ALTER TABLE tasks ADD COLUMN origin TEXT NOT NULL DEFAULT 'local'")
                self._conn.execute("PRAGMA user_version = 1")
            if version < 2:
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(tasks)")}
                if "owner" not in columns:
                    self._conn.execute(
                        "ALTER TABLE tasks ADD COLUMN owner TEXT NOT NULL DEFAULT 'default'",
                    )
                self._conn.execute("PRAGMA user_version = 2")
            # Version 3 is written only when a key was actually supplied. A
            # store opened without the key keeps the old version so enabling
            # encryption later still triggers the one-time plaintext migration
            # (which is now an *explicit* operator action — see below).
            cipher = self._cipher
            if cipher is not None and version < 3:
                # ADR 0036: the store **never** migrates implicitly. The
                # signal that says "this file has been sealed" lived *inside*
                # the file (``PRAGMA user_version``), so anyone with write
                # access could forge it — and a forged version made the
                # migration re-seal the attacker's own substituted rows, which
                # the application then served as if the platform had written
                # them. A legacy file and a substituted file are the *same
                # bytes*, so nothing inside the file can tell them apart.
                # The store therefore refuses content it cannot authenticate,
                # and the one-time migration is an explicit operator step
                # (``scripts/seal_plaintext_state.py``).
                if self._has_unsealed_content(cipher):
                    message = (
                        "the local task state is not sealed under the configured key and the "
                        "store does not migrate implicitly: run "
                        "'python AIAssistant/scripts/seal_plaintext_state.py' once to seal it, "
                        "or point AI_STORAGE_KEY at the key the state was written with"
                    )
                    raise StorageDecryptionError(message)
                self._conn.execute("PRAGMA user_version = 3")
            # Version 4 is the **row-bound** sealing: every sealed value is
            # bound to the row it belongs to, and the row's identity columns
            # (owner/classification/status/context_id/origin) are sealed
            # beside them so they cannot be forged. A sealed file that
            # predates it cannot be authenticated: its values are bound to
            # the field name alone, so a value could be moved between rows
            # and its identity columns were plain text. The store refuses it
            # and the operator runs the migration once — the same fail-closed
            # rule ADR 0036 set for the plaintext state.
            if version < ROW_BOUND_SCHEMA_VERSION:
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(tasks)")}
                if "row_identity" not in columns:
                    self._conn.execute("ALTER TABLE tasks ADD COLUMN row_identity TEXT")
                has_rows = self._conn.execute("SELECT 1 FROM tasks LIMIT 1").fetchone() is not None
                if cipher is not None and has_rows:
                    message = (
                        "the local task state predates the row-bound sealing and the store "
                        "does not migrate implicitly: run "
                        "'python AIAssistant/scripts/migrate_state_row_binding.py' once to "
                        "re-bind it, or point AI_STORAGE_KEY at the key the state was written with"
                    )
                    raise StorageDecryptionError(message)
                if cipher is not None:
                    # A store with a key and no rows holds nothing to re-bind:
                    # the content it will hold from now on is sealed in the
                    # row-bound format.
                    self._conn.execute(f"PRAGMA user_version = {ROW_BOUND_SCHEMA_VERSION}")
            # From here on the store is in the *sealed regime*: every content
            # column was written through the cipher, so a value that is not a
            # sealed envelope is not authentic and is refused on read. The
            # binary artifacts already refuse an unsealed file for exactly
            # this reason (``open_bytes``); the text columns now behave the
            # same way.
            self._sealed_state = cipher is not None

    def _has_unsealed_content(self, cipher: FieldCipher) -> bool:
        """Whether any content column or event payload is not sealed.

        The check is over **every** row, not a sample: a partially sealed
        file would pass a spot check and then serve one plaintext row.
        """
        for message, result, error, metadata in self._conn.execute(
            "SELECT message, result, error, metadata FROM tasks",
        ).fetchall():
            for value in (message, result, error, metadata):
                if value is not None and not cipher.is_sealed(value):
                    return True
        for (data,) in self._conn.execute("SELECT data FROM task_events").fetchall():
            if not cipher.is_sealed(data):
                return True
        return False

    def seal_plaintext_content(self, cipher: FieldCipher) -> int:
        """Seal every unsealed content value under ``cipher`` — the explicit,
        one-time migration (ADR 0036).

        This method is **not** called by the application. It is the
        operator's one-time action for a state file that predates
        encryption, and it exists *because* the implicit migration was
        removed: a store that migrates on open cannot tell a legacy file
        from a substituted one, because the two are the same bytes.

        It seals **plaintext** content only. A file whose content is already
        sealed was written in an *older* sealing format, and re-sealing it
        under the current context is the row-binding migration's job — doing it
        half-way (the identity written, the content left in the old format)
        would leave a file that claims the new version and cannot be read.
        The method therefore **refuses** and names the right action.

        The whole migration runs in **one transaction**: a value that cannot
        be read aborts it and leaves the state exactly as it was. Returns the
        number of rows whose content was sealed.
        """
        sealed_rows = 0
        with self._lock, self._conn:
            for (task_id, message, result, error, metadata, owner, classification,
                 status, context_id, origin) in self._conn.execute(
                "SELECT id, message, result, error, metadata, owner, classification, status, "
                "context_id, origin FROM tasks ORDER BY rowid",
            ).fetchall():
                for value in (message, result, error, metadata):
                    if value is not None and cipher.is_sealed(value):
                        message = (
                            f"task {task_id} is already sealed under another format: run "
                            "'python AIAssistant/scripts/migrate_state_row_binding.py' instead "
                            "(re-sealing it here would leave the content unreadable)"
                        )
                        raise StorageDecryptionError(message)
                sealed = (
                    _seal_optional(cipher, message, task_context(task_id, "message")),
                    _seal_optional(cipher, result, task_context(task_id, "result")),
                    _seal_optional(cipher, error, task_context(task_id, "error")),
                    _seal_optional(cipher, metadata, task_context(task_id, "metadata")),
                )
                identity = _seal_identity_bundle(
                    cipher, task_id,
                    {
                        "owner": owner,
                        "classification": classification,
                        "status": status,
                        "context_id": context_id or "",
                        "origin": origin,
                    },
                )
                self._conn.execute(
                    "UPDATE tasks SET message=?, result=?, error=?, metadata=?, row_identity=? "
                    "WHERE id=?",
                    (*sealed, identity, task_id),
                )
                sealed_rows += 1
            for rowid, task_id, data in self._conn.execute(
                "SELECT rowid, task_id, data FROM task_events ORDER BY rowid",
            ).fetchall():
                if cipher.is_sealed(data):
                    message = (
                        f"task {task_id} has an event payload that is already sealed under "
                        "another format: run 'python AIAssistant/scripts/"
                        "migrate_state_row_binding.py' instead"
                    )
                    raise StorageDecryptionError(message)
                self._conn.execute(
                    "UPDATE task_events SET data=? WHERE rowid=?",
                    (_seal_optional(cipher, data, event_context(task_id)), rowid),
                )
                sealed_rows += 1
            self._conn.execute(f"PRAGMA user_version = {ROW_BOUND_SCHEMA_VERSION}")
        return sealed_rows

    # ── TaskStore protocol ─────────────────────────────────────────────────────

    def commit(
        self, task: AgentTask, *,
        expected: TaskStatus | None = None,
        events: Sequence[tuple[str, dict[str, Any]]] = (),
    ) -> bool:
        """Persist the task and its events in one transaction (ADR 0032).

        The state write and the event append are the same fact observed
        twice. Two separate transactions break both halves of that promise: a
        crash between them leaves a task that is ``completed`` with no
        terminal event, and a subscriber waits forever for an event that will
        never arrive. The adapter already knew the pattern — ``fail_orphaned``
        below does it in one transaction — so the port now *asks* for it
        instead of letting the application layer write two statements and
        hope.

        ``expected is None`` inserts; a status makes the write a
        compare-and-set and the return value says whether it landed.
        """
        now = _now()
        result_json, error, metadata_json = _content_columns(task, self._cipher)
        sealed_events = [
            (
                task.id,
                kind,
                _seal_text(self._cipher, json.dumps(data, default=str), event_context(task.id)),
                now,
            )
            for kind, data in events
        ]
        with self._lock, self._conn:
            if expected is None:
                self._conn.execute(
                    "INSERT INTO tasks (id, capability, message, status, result, error, metadata, context_id, "
                    "classification, origin, owner, row_identity, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task.id, task.capability,
                     _seal_text(self._cipher, task.message, task_context(task.id, "message")),
                     str(task.status),
                     result_json, error, metadata_json,
                     task.context_id,
                     task.classification.value, task.origin.value, task.owner,
                     _seal_identity(self._cipher, task), now, now),
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE tasks SET status=?, result=?, error=?, metadata=?, row_identity=?, updated_at=? "
                    "WHERE id=? AND status=?",
                    (str(task.status), result_json, error, metadata_json,
                     _seal_identity(self._cipher, task), now, task.id, str(expected)),
                )
                if cursor.rowcount != 1:
                    # Lost the race: nothing was written, and no event may be
                    # written either — the event log must not announce a
                    # transition that did not happen.
                    return False
            if sealed_events:
                self._conn.executemany(
                    "INSERT INTO task_events (task_id, kind, data, created_at) VALUES (?, ?, ?, ?)",
                    sealed_events,
                )
        return True

    def create(self, task: AgentTask) -> None:
        now = _now()
        result_json, error, metadata_json = _content_columns(task, self._cipher)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tasks (id, capability, message, status, result, error, metadata, context_id, "
                "classification, origin, owner, row_identity, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.id, task.capability,
                 _seal_text(self._cipher, task.message, task_context(task.id, "message")),
                 str(task.status),
                 result_json, error, metadata_json,
                 task.context_id,
                 task.classification.value, task.origin.value, task.owner,
                 _seal_identity(self._cipher, task), now, now),
            )

    def get(self, task_id: str) -> AgentTask | None:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, capability, message, status, result, error, metadata, context_id, "
                "classification, origin, owner, row_identity, created_at, updated_at "
                "FROM tasks WHERE id = ?", (task_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_task(row, self._cipher, require_sealed=self._sealed_state)

    def save(self, task: AgentTask) -> None:
        now = _now()
        result_json, error, metadata_json = _content_columns(task, self._cipher)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET status=?, result=?, error=?, metadata=?, row_identity=?, updated_at=? "
                "WHERE id=?",
                (str(task.status), result_json, error, metadata_json,
                 _seal_identity(self._cipher, task), now, task.id),
            )

    def save_if_status(self, task: AgentTask, expected: TaskStatus) -> bool:
        """Compare-and-set update: one statement, checked rowcount (no TOCTOU)."""
        now = _now()
        result_json, error, metadata_json = _content_columns(task, self._cipher)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE tasks SET status=?, result=?, error=?, metadata=?, row_identity=?, updated_at=? "
                "WHERE id=? AND status=?",
                (str(task.status), result_json, error, metadata_json,
                 _seal_identity(self._cipher, task), now, task.id, str(expected)),
            )
        return cursor.rowcount == 1

    def fail_orphaned(self, error: str = "Task was interrupted by a process restart") -> int:
        """Fail tasks a previous process left non-terminal.

        A daemon worker cannot survive the process, so ``submitted``/``working``
        rows are guaranteed stale when a new ``TaskService`` opens the store.
        The update and the status event are one transaction: a caller that
        paginates the event log sees the terminal transition exactly once.
        """
        now = _now()
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id, owner, classification, context_id, origin FROM tasks "
                "WHERE status IN ('submitted', 'working')",
            ).fetchall()
            if not rows:
                return 0
            for task_id, owner, classification, context_id, origin in rows:
                sealed_error = _seal_text(self._cipher, error, task_context(task_id, "error"))
                sealed_event = _seal_text(
                    self._cipher, json.dumps({"status": "failed", "error": error}),
                    event_context(task_id),
                )
                identity = _seal_identity_bundle(
                    self._cipher, task_id,
                    {
                        "owner": owner,
                        "classification": classification,
                        "status": "failed",
                        "context_id": context_id or "",
                        "origin": origin,
                    },
                )
                self._conn.execute(
                    "UPDATE tasks SET status='failed', error=?, row_identity=?, updated_at=? WHERE id=?",
                    (sealed_error, identity, now, task_id),
                )
                self._conn.execute(
                    "INSERT INTO task_events (task_id, kind, data, created_at) VALUES (?, ?, ?, ?)",
                    (task_id, "status", sealed_event, now),
                )
        return len(rows)

    def append_event(self, task_id: str, kind: str, data: dict[str, Any]) -> None:
        sealed = _seal_text(self._cipher, json.dumps(data, default=str), event_context(task_id))
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO task_events (task_id, kind, data, created_at) VALUES (?, ?, ?, ?)",
                (task_id, kind, sealed, _now()),
            )

    def events(self, task_id: str, *, since: int = 0) -> Iterable[dict[str, Any]]:
        """Stream only the events after the ``rowid`` cursor (O(new), not O(n)).

        The ``seq`` key is the cursor itself, so SSE subscribers can pass it
        back instead of re-reading and re-slicing the whole log every poll.
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT rowid, kind, data, created_at FROM task_events "
                "WHERE task_id = ? AND rowid > ? ORDER BY rowid",
                (task_id, int(since)),
            )
            rows = cursor.fetchall()
        for rowid, kind, data_json, created_at in rows:
            yield {
                "seq": rowid,
                "kind": kind,
                "data": json.loads(
                    _open_text(self._cipher, data_json, event_context(task_id),
                               require_sealed=self._sealed_state)
                ),
                "created_at": created_at,
            }

    def list(self, *, context_id: str | None = None, limit: int = 100,
             owner: str | None = None) -> Iterable[AgentTask]:
        with self._lock:
            if context_id is not None and owner is not None:
                cursor = self._conn.execute(
                    "SELECT id, capability, message, status, result, error, metadata, context_id, "
                    "classification, origin, owner, row_identity, created_at, updated_at "
                    "FROM tasks WHERE context_id = ? AND owner = ? ORDER BY created_at DESC LIMIT ?",
                    (context_id, owner, limit),
                )
            elif context_id is not None:
                cursor = self._conn.execute(
                    "SELECT id, capability, message, status, result, error, metadata, context_id, "
                    "classification, origin, owner, row_identity, created_at, updated_at "
                    "FROM tasks WHERE context_id = ? ORDER BY created_at DESC LIMIT ?",
                    (context_id, limit),
                )
            elif owner is not None:
                cursor = self._conn.execute(
                    "SELECT id, capability, message, status, result, error, metadata, context_id, "
                    "classification, origin, owner, row_identity, created_at, updated_at "
                    "FROM tasks WHERE owner = ? ORDER BY created_at DESC LIMIT ?",
                    (owner, limit),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT id, capability, message, status, result, error, metadata, context_id, "
                    "classification, origin, owner, row_identity, created_at, updated_at "
                    "FROM tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = cursor.fetchall()
        for row in rows:
            yield _row_to_task(row, self._cipher, require_sealed=self._sealed_state)

    def prune(self, retention_days: int) -> int:
        """Delete terminal tasks older than the window, events included."""
        if retention_days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        statuses = (
            TaskStatus.COMPLETED.value, TaskStatus.FAILED.value,
            TaskStatus.CANCELED.value, TaskStatus.REJECTED.value,
        )
        parameters = (*statuses, cutoff)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM task_events WHERE task_id IN ("
                "SELECT id FROM tasks WHERE status IN (?, ?, ?, ?) AND updated_at < ?)",
                parameters,
            )
            cursor = self._conn.execute(
                "DELETE FROM tasks WHERE status IN (?, ?, ?, ?) AND updated_at < ?",
                parameters,
            )
            return int(cursor.rowcount)

    def rekey(self, old: FieldCipher, new: FieldCipher) -> int:
        """Re-encrypt every sealed column and event payload under a new key.

        Returns the number of rows whose content was re-sealed (task rows and
        event rows). The whole rotation runs in **one transaction**: a value
        the old key cannot open aborts it, and the database is left exactly
        as it was — a half-re-encrypted store would be unreadable with either
        key (ADR 0030).

        Re-running the same pair is safe: a value that is already bound to
        the new key is left untouched instead of being sealed twice.

        The cipher the store itself reads with is **not** changed here — the
        rotation is an operator action and the application is restarted with
        the new key afterwards.
        """
        rekeyed = 0
        with self._lock, self._conn:
            for (task_id, message, result, error, metadata, row_identity, owner, classification,
                 status, context_id, origin) in self._conn.execute(
                "SELECT id, message, result, error, metadata, row_identity, owner, classification, "
                "status, context_id, origin FROM tasks ORDER BY rowid",
            ).fetchall():
                new_message, message_changed = reseal_text(
                    old, new, message, context=task_context(task_id, "message"),
                )
                new_result, result_changed = _reseal_optional(
                    old, new, result, task_context(task_id, "result"),
                )
                new_error, error_changed = _reseal_optional(
                    old, new, error, task_context(task_id, "error"),
                )
                new_metadata, metadata_changed = reseal_text(
                    old, new, metadata, context=task_context(task_id, "metadata"),
                )
                new_identity, identity_changed = _reseal_optional(
                    old, new, row_identity, task_context(task_id, "identity"),
                )
                if new_identity is None:
                    # A row that was written before the identity existed (or
                    # by a plaintext store) gets one now: without it the row
                    # could not be authenticated.
                    new_identity = _seal_identity_bundle(
                        new, task_id,
                        {
                            "owner": owner,
                            "classification": classification,
                            "status": status,
                            "context_id": context_id or "",
                            "origin": origin,
                        },
                    )
                    identity_changed = True
                if (message_changed or result_changed or error_changed or metadata_changed
                        or identity_changed):
                    self._conn.execute(
                        "UPDATE tasks SET message=?, result=?, error=?, metadata=?, row_identity=? "
                        "WHERE id=?",
                        (new_message, new_result, new_error, new_metadata, new_identity, task_id),
                    )
                    rekeyed += 1
            for rowid, task_id, data in self._conn.execute(
                "SELECT rowid, task_id, data FROM task_events ORDER BY rowid",
            ).fetchall():
                new_data, changed = reseal_text(old, new, data, context=event_context(task_id))
                if changed:
                    self._conn.execute(
                        "UPDATE task_events SET data=? WHERE rowid=?", (new_data, rowid),
                    )
                    rekeyed += 1
            # The content is sealed under the new key in the row-bound format:
            # the file says so from now on.
            self._conn.execute(f"PRAGMA user_version = {ROW_BOUND_SCHEMA_VERSION}")
        return rekeyed

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch(stored: str) -> float:
    """Parse a stored ISO-8601 instant back into the domain's epoch seconds.

    Without this the row mapper rebuilt both timestamps from ``time()``, so a
    restart re-dated every task to the moment it was read — and the A2A payload
    answers with ``updated_at``, so clients would see the original creation
    and status times replaced by the restart.
    """
    return datetime.fromisoformat(stored).timestamp()


def _content_columns(task: AgentTask, cipher: FieldCipher | None) -> tuple[str | None, str | None, str]:
    """Serialize and seal the three nullable/JSON content columns.

    The associated data carries the row id, so a sealed value belongs to
    exactly one task.
    """
    result_json = json.dumps(task.result, default=str) if task.result else None
    metadata_json = json.dumps(dict(task.metadata), default=str)
    return (
        _seal_optional(cipher, result_json, task_context(task.id, "result")),
        _seal_optional(cipher, task.error, task_context(task.id, "error")),
        _seal_text(cipher, metadata_json, task_context(task.id, "metadata")),
    )


def _seal_identity_bundle(
    cipher: FieldCipher | None, task_id: str, bundle: Mapping[str, str],
) -> str | None:
    """Seal one row's identity so the plain-text columns cannot be forged."""
    if cipher is None:
        return None
    return cipher.seal(
        json.dumps(dict(bundle), sort_keys=True),
        context=task_context(task_id, "identity"),
    )


def _seal_identity(cipher: FieldCipher | None, task: AgentTask) -> str | None:
    """Seal the row's identity so the plain-text columns cannot be forged."""
    return _seal_identity_bundle(cipher, task.id, _identity_bundle(task))


def _seal_text(cipher: FieldCipher | None, value: str, context: str) -> str:
    return value if cipher is None else cipher.seal(value, context=context)


def _seal_optional(cipher: FieldCipher | None, value: str | None, context: str) -> str | None:
    return None if value is None else _seal_text(cipher, value, context)


def _reseal_optional(
    old: FieldCipher,
    new: FieldCipher,
    value: str | None,
    context: str,
) -> tuple[str | None, bool]:
    """Re-seal a nullable column; ``NULL`` stays ``NULL`` (ADR 0030)."""
    if value is None:
        return None, False
    return reseal_text(old, new, value, context=context)


def _open_text(cipher: FieldCipher | None, value: str, context: str, *,
               require_sealed: bool = False) -> str:
    """Decrypt one content column.

    ``require_sealed`` is the store's *sealed regime* flag: once the store
    has migrated its rows, a value that is not a sealed envelope is not
    authentic. Serving it would be an AEAD downgrade — the GCM tag would
    never get the chance to reject the substituted payload, and the caller
    would receive it as if the platform had written it.
    """
    if cipher is None:
        if is_sealed(value):
            message = "encrypted state cannot be read without AI_STORAGE_KEY or its key file"
            raise StorageKeyError(message)
        return value
    if require_sealed and not cipher.is_sealed(value):
        message = f"{context} is not a sealed envelope"
        raise StorageDecryptionError(message)
    return cipher.open(value, context=context)


def _open_optional(cipher: FieldCipher | None, value: str | None, context: str, *,
                   require_sealed: bool = False) -> str | None:
    return None if value is None else _open_text(cipher, value, context, require_sealed=require_sealed)


def _row_to_task(row: tuple[str, ...], cipher: FieldCipher | None, *,
                 require_sealed: bool = False) -> AgentTask:
    (tid, capability, message, status_str, result_json, error, metadata_json, context_id,
     classification, origin, owner, row_identity, created_at, updated_at) = row
    # The identity is authenticated *before* the row is served: the sealed
    # bundle has to match the plain-text columns. A mismatch means the row was
    # edited outside the platform — the owner was changed, the classification
    # downgraded or the status forged — and the row is refused.
    _verify_identity(
        tid, row_identity, cipher, require_sealed=require_sealed,
        columns={
            "owner": owner,
            "classification": classification,
            "status": status_str,
            "context_id": context_id or "",
            "origin": origin,
        },
    )
    return AgentTask(
        id=tid,
        capability=capability,
        message=_open_text(cipher, message, task_context(tid, "message"),
                           require_sealed=require_sealed),
        context_id=context_id,
        metadata=json.loads(
            _open_text(cipher, metadata_json, task_context(tid, "metadata"),
                       require_sealed=require_sealed)
        ) if metadata_json else {},
        # Parsed, not defaulted: the classification decides whether this task may
        # leave the trust boundary, and an unparseable value must fail loudly
        # rather than come back promoted to the permissive default.
        classification=DataClassification(classification),
        status=TaskStatus(status_str),
        origin=ExecutionOrigin(origin),
        owner=owner,
        created_at=_epoch(created_at),
        updated_at=_epoch(updated_at),
        result=json.loads(
            _open_text(cipher, result_json, task_context(tid, "result"),
                       require_sealed=require_sealed)
        ) if result_json else None,
        error=_open_optional(cipher, error, task_context(tid, "error"),
                             require_sealed=require_sealed),
    )


def _verify_identity(
    task_id: str,
    sealed_identity: str | None,
    cipher: FieldCipher | None,
    *,
    require_sealed: bool,
    columns: Mapping[str, str],
) -> None:
    """Refuse a row whose identity does not match its sealed bundle.

    Without a key there is nothing to authenticate against (the plaintext
    store is the operator's own choice), so the check is skipped. With a key
    the bundle is mandatory: a row that carries none has never been written
    by this platform, and a row whose bundle disagrees with its columns was
    edited outside it.
    """
    if cipher is None:
        return
    if not sealed_identity:
        message = f"task {task_id} carries no authenticated identity"
        raise StorageDecryptionError(message)
    bundle = json.loads(
        _open_text(cipher, sealed_identity, task_context(task_id, "identity"),
                   require_sealed=require_sealed)
    )
    for key, actual in columns.items():
        if bundle.get(key) != actual:
            message = f"task {task_id} identity does not match its sealed row"
            raise StorageDecryptionError(message)
