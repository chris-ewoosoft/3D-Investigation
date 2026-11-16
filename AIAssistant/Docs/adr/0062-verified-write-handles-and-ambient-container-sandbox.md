# ADR 0062: Verified write handles and ambient-container sandbox parity

## Context

The write policy canonicalised and authorised a path, then reopened the same
string for `mkdir`, temporary-file creation and `os.replace`. A concurrent
symlink, junction or parent-directory swap could therefore make the commit
operate on a different filesystem object. Read paths already had an
opened-handle identity gate (ADR 0058), but write/create paths explicitly did
not.

The production compose profile also disabled the command sandbox because
starting Docker from inside the service would require the host daemon socket.
That left container deployments with fewer agent capabilities than desktop,
even though the service was already inside a read-only, capability-dropped
container.

## Decision

1. `tools/verified_write.py` owns filesystem mutation after policy approval.
   On Windows it opens every directory component with
   `FILE_FLAG_OPEN_REPARSE_POINT`, rejects reparse/non-directory components,
   verifies the final path from the handle and keeps the handle open without
   `FILE_SHARE_DELETE` until commit. On POSIX it walks with directory file
   descriptors and `O_NOFOLLOW`; temporary creation and `os.replace` are
   relative to the same final `dir_fd`.
2. `sandbox.write_file` and `create_directory` pass only the canonical root
   plus an authorised relative name to that module. They never reopen the
   caller's original path. Temporary files are exclusive, flushed, atomically
   renamed and removed on failure.
3. Compose enables the sandbox with `AGENT_SANDBOX_RUNTIME=container`. This
   runtime is accepted as isolated only when Linux exposes `/.dockerenv` or a
   Docker/Kubernetes/containerd cgroup marker. Merely setting an environment
   value cannot claim isolation. The host Docker socket remains unmounted.
4. Native mutation mechanics are separated from the command-policy module.
   The OS-specific code has its own tests and mutation floor, keeping another
   responsibility out of the existing sandbox hotspot.

## Verification

`tests/unit/test_verified_write.py` exercises real Windows directory handles,
atomic replacement, identity mismatch, partial writes, failure cleanup and a
fully mocked POSIX descriptor walk. `tests/unit/test_sandbox.py` pins both
descriptor-anchored delegation and ambient-container admission. Five decision
mutants raise `MIN_MUTANTS` from 339 to 344; `verified_write.py` is in
`PINNED_DECISION_FILES`.

## Consequences

The application now commits to the directory objects it opened and verified,
not to a path string that can be retargeted between check and use. This closes
the write/create symlink and junction TOCTOU class inside the application.
Whole-host protection still belongs to the OS boundary: a process already
running as the same account can manipulate objects outside this API, while a
container deployment remains constrained by its read-only root filesystem,
dropped capabilities and `no-new-privileges` profile.

