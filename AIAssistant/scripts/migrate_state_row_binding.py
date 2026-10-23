"""Re-bind the local task state to its row (row-bound at-rest sealing).

Why this is a script and not something the application does on open
--------------------------------------------------------------------------
The state was sealed with the **field name** as the AEAD associated data:
``task.message``, ``task.result``, ``event.data``. That authenticated the
*column* but not the *row*. A ciphertext copied from one task to another
opened perfectly and was then served as the second task's content — and the
columns that decide who may read a row (``owner``, ``classification``,
``status``) were plain text besides, so they could be edited directly.

The state is now sealed with the **row id** in the associated data, and each
row carries a sealed identity bundle beside its plain-text columns. A store
that predates that cannot authenticate its own rows, so it refuses the file
and names this script instead of migrating it implicitly: a legacy file and a
substituted file are the same bytes, and only the operator can say which one
it is (ADR 0036's rule, applied to the row binding).

What it does
------------
* opens every task column with the key, using the **old** context when the
  value carries it and the new one otherwise (so the script is idempotent);
* re-seals it with the row-bound context and writes the row's identity;
* re-seals every event payload with the context of its **owning task**, so an
  event cannot be reparented to another task's stream;
* marks the store as row-bound (``PRAGMA user_version = 4``);
* runs in **one transaction** — a value that cannot be read aborts it and
  leaves the state exactly as it was.

It never prints key material.

Usage
-----
    python AIAssistant/scripts/migrate_state_row_binding.py --verify
    python AIAssistant/scripts/migrate_state_row_binding.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_assistant.adapters.persistence.sqlite_store import (
    ROW_BOUND_SCHEMA_VERSION,
    event_context,
    task_context,
)
from ai_assistant.adapters.persistence.storage_crypto import (
    AesGcmFieldCipher,
    StorageCryptoError,
    is_sealed,
)
from ai_assistant.config.paths import get_paths
from ai_assistant.settings import storage_key

TASK_DB_NAME = "tasks.sqlite"

#: The associated data the *previous* format used: the field name alone.
LEGACY_TASK_CONTEXTS = {
    "message": "task.message",
    "result": "task.result",
    "error": "task.error",
    "metadata": "task.metadata",
}
LEGACY_EVENT_CONTEXT = "event.data"


def _state_path() -> Path:
    return get_paths().data_dir / TASK_DB_NAME


def _configured_cipher() -> AesGcmFieldCipher:
    """The cipher the application itself would use.

    Read through the same settings the composition root reads, so the
    script cannot drift from the application into re-binding the state with a
    *different* key than the one the app opens it with.
    """
    configured = storage_key()
    if not configured:
        message = (
            "no at-rest key is configured: set AI_STORAGE_KEY or AI_STORAGE_KEY_FILE "
            "before re-binding the local state"
        )
        raise StorageCryptoError(message)
    from ai_assistant.adapters.persistence.storage_crypto import cipher_from_key_text

    return cipher_from_key_text(configured, source="AI_STORAGE_KEY")


def _rebind_value(
    cipher: AesGcmFieldCipher, value: str | None, task_id: str, field: str,
) -> str | None:
    """Re-seal one task column with the row-bound context."""
    if value is None:
        return None
    context = task_context(task_id, field)
    if not is_sealed(value):
        # A legacy plaintext column: the operator's explicit action seals it.
        return cipher.seal(value, context=context)
    try:
        cipher.open(value, context=context)
    except StorageCryptoError:
        pass
    else:
        return value  # already row-bound: the script is idempotent
    try:
        plaintext = cipher.open(value, context=LEGACY_TASK_CONTEXTS[field])
    except StorageCryptoError as error:
        message = (
            f"task {task_id}: the '{field}' column cannot be opened with the configured key"
        )
        raise StorageCryptoError(message) from error
    return cipher.seal(plaintext, context=context)


def _rebind_event(
    cipher: AesGcmFieldCipher, value: str, task_id: str,
) -> str:
    """Re-seal one event payload with the context of its owning task."""
    context = event_context(task_id)
    try:
        cipher.open(value, context=context)
    except StorageCryptoError:
        pass
    else:
        return value
    try:
        plaintext = cipher.open(value, context=LEGACY_EVENT_CONTEXT)
    except StorageCryptoError as error:
        message = f"task {task_id}: an event payload cannot be opened with the configured key"
        raise StorageCryptoError(message) from error
    return cipher.seal(plaintext, context=context)


def _identity(cipher: AesGcmFieldCipher, task_id: str, row: tuple[str, ...]) -> str:
    """Seal the row's identity from its plain-text columns."""
    owner, classification, status, context_id, origin = row
    bundle = json.dumps(
        {
            "owner": owner,
            "classification": classification,
            "status": status,
            "context_id": context_id or "",
            "origin": origin,
        },
        sort_keys=True,
    )
    return cipher.seal(bundle, context=task_context(task_id, "identity"))


def _rebind(db_path: Path, cipher: AesGcmFieldCipher) -> int:
    """Re-bind every row and event (one transaction)."""
    connection = sqlite3.connect(str(db_path))
    try:
        with connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
            if "row_identity" not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN row_identity TEXT")
            rows = connection.execute(
                "SELECT id, message, result, error, metadata, owner, classification, status, "
                "context_id, origin FROM tasks ORDER BY rowid",
            ).fetchall()
            events = connection.execute(
                "SELECT rowid, task_id, data FROM task_events ORDER BY rowid",
            ).fetchall()
            for task_id, message, result, error, metadata, owner, classification, status, \
                    context_id, origin in rows:
                sealed = (
                    _rebind_value(cipher, message, task_id, "message"),
                    _rebind_value(cipher, result, task_id, "result"),
                    _rebind_value(cipher, error, task_id, "error"),
                    _rebind_value(cipher, metadata, task_id, "metadata"),
                )
                identity = _identity(
                    cipher, task_id, (owner, classification, status, context_id, origin),
                )
                connection.execute(
                    "UPDATE tasks SET message=?, result=?, error=?, metadata=?, row_identity=? "
                    "WHERE id=?",
                    (*sealed, identity, task_id),
                )
            for rowid, task_id, data in events:
                connection.execute(
                    "UPDATE task_events SET data=? WHERE rowid=?",
                    (_rebind_event(cipher, data, task_id), rowid),
                )
            connection.execute(f"PRAGMA user_version = {ROW_BOUND_SCHEMA_VERSION}")
    finally:
        connection.close()
    return len(rows)


def _verify(db_path: Path) -> int:
    """Report whether the state is already bound to its row."""
    connection = sqlite3.connect(str(db_path))
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
    print(f"  state format version      : {version} "
          f"(row-bound is {ROW_BOUND_SCHEMA_VERSION})")
    print("")
    if version >= ROW_BOUND_SCHEMA_VERSION:
        print("ROW-BOUND — the local state is bound to its rows.")
        return 0
    print("NOT ROW-BOUND — run this script without --verify to re-bind it.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-bind the local task state to its row under the configured at-rest key.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="report whether the local state is row-bound; change nothing.",
    )
    args = parser.parse_args(argv)

    db_path = _state_path()
    if not db_path.exists():
        print(f"no local task state found under {get_paths().data_dir}")
        return 0
    if args.verify:
        return _verify(db_path)

    cipher = _configured_cipher()
    rows = _rebind(db_path, cipher)
    print("REBIND OK")
    print(f"  task rows re-bound         : {rows}")
    print("")
    print("The assistant can now open the state with the configured key.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
