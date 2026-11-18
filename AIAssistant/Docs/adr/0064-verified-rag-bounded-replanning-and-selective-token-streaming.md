# ADR 0064: Verified RAG, bounded re-planning and selective token streaming

Date: 2026-11-17

## Status

Accepted.

## Context

The agent exposed RAG as formatted text, treated reflection as a mostly binary
retry loop, and delivered visible output only after a graph node completed.
This made weak retrieval difficult to reject, allowed correction loops to spend
an implicit budget, and made a long final generation appear stalled.

Streaming every model call is unsafe: planner, router and critic outputs are
structured control data and may contain hidden reasoning or incomplete tool
arguments. The desktop also needs one canonical terminal response for durable
chat history.

## Decision

- RAG returns typed hits with stable chunk identity, repository-relative
  provenance, ranking signals and an answerability decision. Weak or missing
  evidence fails closed. Retrieved text is untrusted data.
- Reflection uses a closed decision vocabulary. Re-planning and tool calls have
  explicit limits stored in graph state.
- Token streaming is enabled only after routing has established that the next
  completion is user-visible prose. Structured completions remain atomic and
  `<think>` content is filtered across chunk boundaries.
- SSE uses ordered `status`, `delta`, `step`, `done` and `error` events through
  a bounded queue. Qt renders deltas as an ephemeral draft; `done` replaces the
  draft with the canonical response.

## Consequences

Time to first visible token improves for final and evidence-synthesis answers,
without exposing partial control JSON. Accuracy improves where provenance and
the no-answer gate prevent unsupported synthesis. Extra status events and
reranking metadata add small CPU/serialization overhead, and remote backends
that do not expose streaming still emit one complete delta.

Six mutation tests pin answerability, both workflow budgets, selective
streaming, hidden-reasoning filtering and SSE event sanitization.
