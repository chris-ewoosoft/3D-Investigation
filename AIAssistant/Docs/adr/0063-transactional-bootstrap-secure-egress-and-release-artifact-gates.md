# ADR 0063: Transactional bootstrap, secure egress and release-artifact gates

## Context

The source gates were green while three release boundaries remained weaker
than their claims. The Docker image copied only `AIAssistant/`, although tool
composition reads two repository-level `Config/*.json` contracts. The lazy
composition root marked itself complete before the object graph was ready and
did not close resources allocated before a later failure. Finally, an
allowlisted A2A peer could use plaintext HTTP while receiving the outbound
bearer token. CI tested source behavior but neither bootstrapped the packaged
contracts nor rejected known dependency/image vulnerabilities.

## Decision

1. The release image explicitly packages `agent_tools.json` and
   `agent_action_manifest.json`. Its build context allowlist names both files,
   and CI constructs the real tool registry and action manifest inside the
   built image.
2. `compose()` is a two-phase transaction under one re-entrant lock. It
   serializes concurrent callers, refuses same-thread re-entry, marks the graph
   ready only after every router is mounted, and on failure closes stream,
   task, tool, checkpoint and memory owners before restoring the previous
   complete module state.
3. Authenticated A2A discovery and dispatch require an unambiguous HTTPS base
   URL by default. Plain HTTP exists only behind
   `AI_A2A_ALLOW_INSECURE_HTTP=1`, an explicit development override shared by
   registry and client policy.
4. Python CI and the local pre-push hook share `dependency_audit.py`, which
   audits all five hash-pinned locks with hash-pinned `pip-audit`. Exceptions
   must name one advisory and document both exposure and compensating control;
   the initial no-fix exceptions are the write-access prerequisite in
   DiskCache and the offline-only RAGAS evaluation environment. CI also scans
   the built image with a commit-pinned Trivy action for high/critical OS and
   library vulnerabilities.
5. The runtime stage starts from the current digest-pinned Python slim image,
   upgrades APT/zlib to their minimum fixed Debian versions and asserts those
   versions during the build. Before the locked application prefix is copied,
   it removes the base setuptools tree so obsolete vendored metadata cannot
   survive Docker's overlay copy and create real or stale scanner findings.

## Verification

Docker/document contract tests pin the shared manifests, smoke job and scan
steps. A2A tests prove plaintext refusal occurs before any network call and
exercise the explicit override. Composition tests race two callers, force a
late allocation failure, inject a failing close and verify ordered best-effort
rollback. Nine new decision mutants pin HTTPS-only egress plus composition
commit, re-entry, rollback and every resource owner, raising `MIN_MUTANTS`
from 344 to 353.

## Consequences

A green source suite is no longer enough to publish an unusable or known-
vulnerable container. Startup either exposes one complete graph or none, and
an A2A bearer cannot silently traverse plaintext transport. Dependency and
image audits rely on current external vulnerability databases, so CI needs
network access and can become red when a newly disclosed issue affects an
otherwise unchanged commit; that is the intended release behavior.
