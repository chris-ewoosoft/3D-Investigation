# ADR 0059: Close container, memory and concurrency trust gaps

Date: 2026-11-15

## Status

Accepted.

## Context

An independent source review found that several individually reasonable guards
did not compose safely: Docker NAT changed the apparent peer address; public
task metadata could reuse the internal continuation namespace; recalled model
output entered the system prompt without an untrusted-data boundary; two model
reloads could overlap; memory paths depended on the process working directory;
sealed state files remained inside an approved write tree; A2A had an unused
SDK/environment availability gate; and short-term memory reads did not verify
the owner.

## Decision

- The container uses a fixed private bridge and explicitly trusts only its
  gateway `/32`. Loopback remains the default everywhere else, and bearer and
  origin checks are unchanged.
- `TaskService.submit()` removes every public `agent_run.*` metadata key. Only
  application-owned resume paths may create continuation state.
- Semantic preferences are extracted only from user-authored task text.
  Recalled long-term memory is delimited as untrusted data, and a procedural
  seed must pass the current plan critic before tool execution.
- A dedicated reload mutex serializes the complete drain/swap window.
- The composition root resolves the memory database from injected paths and
  centralized runtime settings, never from `cwd` or test-directory name
  heuristics.
- Memory/checkpoint SQLite files and their sidecars are denied by the approved
  write sandbox.
- The built-in A2A transport has no optional SDK probe or `A2A_ENABLED` switch;
  `ArchitectureSettings.enable_a2a` is the single mounting decision.
- Short-term memory reads require both `session_id` and `owner`, with the
  service forwarding the authenticated task owner.

## Verification

Focused transport, task lifecycle, memory, planning, sandbox, reload and A2A
tests pin the behavior. Ten decision mutants cover the new trust and ownership
decisions; the global mutation floor is 327. The documentation contract also
checks the repository-level `AGENTS.md` mutation/ADR claim so it cannot drift
outside the package documentation again.
