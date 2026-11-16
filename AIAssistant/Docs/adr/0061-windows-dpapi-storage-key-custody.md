# ADR 0061: Windows DPAPI storage-key custody

Date: 2026-11-16

## Status

Accepted.

## Context

The service's AES key is data, not a password-derived secret. The default
desktop key file was owner-scoped but still held readable base64. Windows
already provides a stronger account-bound custody primitive: Data Protection
API (DPAPI).

Explicit `AI_STORAGE_KEY` and `AI_STORAGE_KEY_FILE` sources are deployment
inputs. They may be supplied by a KMS or mounted secret and must not be
silently rewritten into a machine-local format.

## Decision

- A newly generated default Windows desktop key is wrapped with user-scope
  DPAPI and stored as `dpapi:v1:<base64(blob)>`. UI prompts are forbidden and
  stable application entropy binds the blob to this purpose.
- A legacy base64 default key is decoded, wrapped and atomically replaced
  before the service uses it. Protection or replacement failure aborts
  startup; the service never continues with plaintext after an OS protection
  failure.
- DPAPI output is released with `LocalFree`. Missing APIs, native failures and
  malformed envelopes become `StorageKeyError` without logging key material.
- Explicit environment and mounted-file keys retain their existing format and
  ownership. POSIX retains the owner-only `0600` fallback because Python has
  no single standard-library keyring equivalent; stronger deployments inject
  a secret through `AI_STORAGE_KEY_FILE`.

## Verification

`tests/unit/test_storage_crypto.py` covers the platform decision, new-key
creation, legacy migration, atomic-failure preservation, envelope validation,
native error containment and buffer release. Windows additionally executes a
real `CryptProtectData`/`CryptUnprotectData` round trip. Five new decision
mutants raise the floor from 334 to 339 and add `key_custody.py` to the
per-file mutation floor.

## Consequences

Copying the default Windows key file and data volume to another account no
longer yields the raw AES key. An attacker already executing as the same
account can still ask DPAPI to unwrap it; process isolation, account security
and volume/backup controls remain deployment boundaries. Rotation of the AES
key remains the transactional operator workflow from ADR 0030.
