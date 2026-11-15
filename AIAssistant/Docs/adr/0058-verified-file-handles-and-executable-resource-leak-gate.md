# ADR 0058 — Verified file handles and an executable resource-leak gate

- Status: Accepted
- Date: 2026-11-15
- Extends: ADR 0033, ADR 0035, ADR 0038, ADR 0056

## Context

The path policy rejected secret *names* and followed symbolic links, but a hard
link has no target path to resolve. A caller could create an allowed-looking
name such as `notes.txt` that referred to the same file identity as `.env`.
The policy accepted the alias and a later `open()` returned the secret.

The read path also performed security checks before opening the file. That
left a check/use interval in which the directory entry could be replaced. The
same pattern existed in agent tools, RAG ingestion, image/attachment loading
and the LSP reader.

Separately, the scorecard said `ResourceWarning` was a failing condition, but
the default unittest and coverage commands used CPython's default warning
filter. The claim was therefore not executable in CI. Turning the warning into
an exception is insufficient for warnings emitted during object finalization,
because those can become unraisable exceptions after a test appears to pass.

## Decision

1. `path_policy.open_verified_regular_file()` is the final handle gate for
   package-owned readers of untrusted filesystem inputs. It verifies the
   opened handle with `fstat`, requires a regular file with exactly one link,
   and confirms that the current path still names the same device/inode
   identity.
2. Enumeration and preflight use `is_single_link_regular_file()`, but callers
   must read from the verified handle. Preflight is not a security authority.
3. Agent file/code/search/system tools, RAG loaders and corpus enumeration,
   image/attachment readers and the LSP client use this shared policy. An AST
   architecture test rejects a direct built-in `open()` in those readers.
4. Multiply-linked files are refused. This intentionally trades support for
   uncommon hard-linked project files for a fail-closed rule that is portable
   across the Windows and Linux jobs.
5. `tests.run_suite` owns test discovery for CI and coverage. It records
   `ResourceWarning`, installs an unraisable hook, forces collection while both
   channels are active and returns a failing process status if either channel
   reports a leak.
6. Two mutants pin the load-bearing decisions: allowing `st_nlink >= 1`, and
   disabling the resource-leak failure branch. `MIN_MUTANTS` rises from 313 to
   315 and both decision files join `PINNED_DECISION_FILES`.

## Scope and limitations

- The verified handle closes the identity check/use gap at the final open:
  data is consumed from the identity that was verified, not from a later
  reopen.
- It does not claim that a directory entry cannot change after verification;
  it claims that such a change cannot redirect the already-open read handle.
- Earlier semantic checks (root confinement, secret names and extension
  policy) remain path-based. A hostile local process that can mutate the tree
  concurrently is outside the in-process read-gate guarantee and requires an
  OS sandbox/ACL boundary; the handle gate must not be described as making
  those separate checks transactionally atomic.
- Write/create operations retain the TOCTOU limitation documented by ADR 0033.
  Closing that boundary requires platform-specific descriptor-relative APIs or
  an external sandbox/container boundary and is not inferred from this ADR.
- The single-link rule protects readers owned by this package. It is not a
  filesystem-wide confidentiality boundary against another process that can
  already open the original secret path.

## Consequences

- A blocked file cannot be smuggled through an allowed hard-link alias into an
  agent response, RAG corpus, attachment, image or LSP read.
- A path replaced during the final open/identity verification is refused; once
  returned, readers consume the already-verified identity.
- CI now measures the resource-ownership claim made by the scorecard.
- Readers that genuinely need hard-linked files require an explicit future
  contract rather than an ad-hoc bypass.

## Verification

- `tests/unit/test_path_policy.py`
- `tests/unit/test_file_tools.py`
- `tests/unit/test_search_tools.py`
- `tests/unit/test_rag_corpus_boundary.py`
- `tests/unit/test_http_chat_routes.py`
- `tests/unit/test_resource_warning_gate.py`
- `tests/integration/test_architecture_boundaries.py`
- `python -m coverage run -m tests.run_suite`
- `python scripts/mutation_check.py --files src/ai_assistant/path_policy.py tests/run_suite.py`
