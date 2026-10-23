"""Pending approvals are sealed at rest, with legacy plaintext still readable."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ai_assistant.adapters.persistence import PendingActionStore
from ai_assistant.adapters.persistence.storage_crypto import (
    AesGcmFieldCipher,
    StorageDecryptionError,
    StorageKeyError,
    is_sealed,
)


def _cipher() -> AesGcmFieldCipher:
    return AesGcmFieldCipher(b"p" * 32)


class EncryptedPendingActionStoreTests(unittest.TestCase):
    def test_save_seals_the_payload_and_load_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            store = PendingActionStore(path, cipher=_cipher())
            store["act-1"] = {"tool": "write_file", "params": {"path": "secret.txt"}}
            store.save()

            raw = path.read_text(encoding="utf-8")
            self.assertTrue(is_sealed(raw))
            self.assertNotIn("write_file", raw)
            self.assertNotIn("secret.txt", raw)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

            reloaded = PendingActionStore(path, cipher=_cipher())
            reloaded.load()
            self.assertEqual(reloaded["act-1"]["tool"], "write_file")

    def test_a_plaintext_pending_file_is_refused_when_a_key_is_configured(self):
        """A store with a key never accepts content it cannot authenticate.

        The file used to be accepted in the clear as a "legacy" file and
        re-sealed on the fly — which meant an attacker with write access
        could replace the whole envelope with a plaintext one and have their
        pending approval served to the user. The pending queue is transient,
        so refusing costs a re-approval, not data.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            path.write_text(json.dumps({"act-1": {"tool": "legacy"}}), encoding="utf-8")
            store = PendingActionStore(path, cipher=_cipher())
            with self.assertRaises(StorageDecryptionError):
                store.load()

    def test_a_plaintext_pending_file_is_still_readable_without_a_key(self):
        """A store *without* a key is a plaintext store by design.

        The refusal is a property of the deployment having enabled
        encryption, not of the file's shape — the opt-in path must keep
        working.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            path.write_text(json.dumps({"act-1": {"tool": "legacy"}}), encoding="utf-8")
            store = PendingActionStore(path)
            store.load()
            self.assertEqual(store["act-1"]["tool"], "legacy")

    def test_the_explicit_migration_seals_a_plaintext_pending_file(self):
        """The one-time migration is an operator action, not an open-time one."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            path.write_text(json.dumps({"act-1": {"tool": "legacy"}}), encoding="utf-8")
            cipher = _cipher()
            self.assertEqual(PendingActionStore(path).rekey(cipher, cipher), 1)

            raw = path.read_text(encoding="utf-8")
            self.assertTrue(raw.startswith("enc:v1:"), "the file must be sealed")
            self.assertNotIn("legacy", raw)

            reloaded = PendingActionStore(path, cipher=_cipher())
            reloaded.load()
            self.assertEqual(reloaded["act-1"]["tool"], "legacy")

    def test_sealed_payload_without_a_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            PendingActionStore(path, cipher=_cipher()).save()
            blind = PendingActionStore(path)
            with self.assertRaises(StorageKeyError):
                blind.load()

    def test_tampered_payload_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            store = PendingActionStore(path, cipher=_cipher())
            store["act-1"] = {"tool": "write_file"}
            store.save()
            raw = path.read_text(encoding="utf-8")
            path.write_text(raw[:-1] + ("A" if raw[-1] != "A" else "B"), encoding="utf-8")

            reloaded = PendingActionStore(path, cipher=_cipher())
            with self.assertRaises(StorageDecryptionError):
                reloaded.load()

    def test_a_corrupt_plaintext_file_is_refused_when_a_key_is_configured(self):
        """Fail closed: an unsealed file is refused before it is parsed.

        The old behaviour treated a corrupt plaintext file as "legacy, read
        it leniently" and dropped the queue with a warning. With a key
        configured, an unsealed file is refused whatever it contains — the
        file's shape is not evidence that the platform wrote it.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            path.write_text("{not json", encoding="utf-8")
            store = PendingActionStore(path, cipher=_cipher())
            with self.assertRaises(StorageDecryptionError):
                store.load()
            self.assertEqual(len(store), 0)

    def test_a_corrupt_plaintext_file_is_dropped_without_a_key(self):
        """Without a key the store is a plaintext store, and a corrupt file
        must not brick startup — it is dropped with a warning."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pending.json"
            path.write_text("{not json", encoding="utf-8")
            store = PendingActionStore(path)
            with self.assertLogs("ai_assistant.persistence.pending_store", "WARNING"):
                store.load()
            self.assertEqual(len(store), 0)


if __name__ == "__main__":
    unittest.main()
