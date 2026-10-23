"""Seal a local state file that predates at-rest encryption (ADR 0036).

Why this is a script and not something the application does on open
--------------------------------------------------------------------------
The application used to migrate a plaintext state file the first time it
opened it with a key. That was unsafe, and not in a subtle way: the signal
that said *"this file has been sealed"* lived **inside the file**
(``PRAGMA user_version``), so anyone with write access to the file could
forge it — and a forged version made the migration re-seal the attacker's own
substituted rows, which the application then served as if the platform had
written them.

A legacy file and a substituted file are **the same bytes**. Nothing inside
the file can tell them apart, so the application cannot decide safely; only
the *operator* can, by saying so explicitly. Hence this script.

What it does
------------
* seals every unsealed task column and event payload under the configured
  key, in **one transaction** — a value that cannot be read aborts it and
  leaves the state exactly as it was;
* seals the pending-action file the same way;
* marks the task store as sealed (``PRAGMA user_version = 3``).

It never prints key material.

Usage
-----
    python AIAssistant/scripts/seal_plaintext_state.py --verify
    python AIAssistant/scripts/seal_plaintext_state.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_assistant.adapters.persistence.pending_store import PendingActionStore
from ai_assistant.adapters.persistence.sqlite_store import SqliteTaskStore
from ai_assistant.adapters.persistence.storage_crypto import (
    AesGcmFieldCipher,
    StorageCryptoError,
)
from ai_assistant.config.paths import get_paths
from ai_assistant.settings import storage_key

TASK_DB_NAME = "tasks.sqlite"
PENDING_NAME = "pending_agent_actions.json"


def _state_paths() -> tuple[Path, Path]:
    data_dir = get_paths().data_dir
    return data_dir / TASK_DB_NAME, data_dir / PENDING_NAME


def _configured_cipher() -> AesGcmFieldCipher:
    """The cipher the application itself would use.

    Read through the same settings the composition root reads, so the
    script cannot drift from the application into sealing with a *different*
    key than the one the app opens with.
    """
    configured = storage_key()
    if not configured:
        message = (
            "no at-rest key is configured: set AI_STORAGE_KEY or AI_STORAGE_KEY_FILE "
            "before sealing the local state"
        )
        raise StorageCryptoError(message)
    from ai_assistant.adapters.persistence.storage_crypto import cipher_from_key_text

    return cipher_from_key_text(configured, source="AI_STORAGE_KEY")


def _seal_task_store(db_path: Path, cipher: AesGcmFieldCipher) -> int:
    """Seal every unsealed value in the task store (one transaction)."""
    store = SqliteTaskStore(db_path)
    try:
        return store.seal_plaintext_content(cipher)
    finally:
        store.close()


def _seal_pending(pending_path: Path, cipher: AesGcmFieldCipher) -> int:
    """Seal the pending-action payload (atomic replace)."""
    store = PendingActionStore(pending_path)
    return store.rekey(cipher, cipher)


def _verify(db_path: Path, pending_path: Path, cipher: AesGcmFieldCipher) -> int:
    """Report whether the local state is already sealed under the key."""
    sealed = True
    if db_path.exists():
        store = SqliteTaskStore(db_path)
        try:
            unsealed = store._has_unsealed_content(cipher)
        finally:
            store.close()
        sealed = sealed and not unsealed
        print(f"  task state sealed       : {'yes' if not unsealed else 'NO'}")
    if pending_path.exists():
        raw = pending_path.read_text(encoding="utf-8")
        is_sealed = raw.strip().startswith("enc:v1:")
        sealed = sealed and is_sealed
        print(f"  pending payload sealed  : {'yes' if is_sealed else 'NO'}")
    print("")
    if sealed:
        print("SEALED — the local state is sealed under the configured key.")
        return 0
    print("NOT SEALED — run this script without --verify to seal it.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seal the local task and pending state under the configured at-rest key.",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="report whether the local state is sealed; change nothing.",
    )
    args = parser.parse_args(argv)

    db_path, pending_path = _state_paths()
    if not db_path.exists() and not pending_path.exists():
        print(f"no local state found under {get_paths().data_dir}")
        return 0
    cipher = _configured_cipher()

    if args.verify:
        return _verify(db_path, pending_path, cipher)

    tasks = 0
    pending = 0
    if db_path.exists():
        tasks = _seal_task_store(db_path, cipher)
    if pending_path.exists():
        pending = _seal_pending(pending_path, cipher)
    print("SEAL OK")
    print(f"  task rows sealed          : {tasks}")
    print(f"  pending payload sealed    : {pending}")
    print("")
    print("The assistant can now open the state with the configured key.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
