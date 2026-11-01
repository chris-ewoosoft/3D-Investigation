# ADR 0050: Balance the model-in-loop release gate by language and planner kind

## Status

Accepted — 2026-11-03.

## Context

The real-model planner gate contained seven prompts. Its aggregate accuracy
made the known `en_003` miss visible, but the sample was unbalanced: one
language or one planner class could regress while the total still cleared a
future percentage floor. A seven-case result is a useful smoke signal, not a
release-grade matrix for the planner's closed three-kind vocabulary.

## Decision

The pinned-model evaluation owns a balanced 24-case matrix:

- languages: Vietnamese and English;
- expected planner kinds: `direct`, `rag_search`, and `application_action`;
- four independently worded prompts in every language × kind slice.

Release CI requires all three conditions:

1. at least 90% model coverage;
2. at least 90% aggregate kind accuracy;
3. at least 75% accuracy in every four-case slice.

The slice floor means a high aggregate cannot conceal a planner that has lost
one language or one kind. The golden-set balance and the slice gate are unit
tested; the slice decision also has a mutation that must be killed.

The prompt states the classification rule in terms of the source of truth the
answer needs. In particular, a technology name alone does not decide the kind:
general knowledge is `direct`, repository-specific knowledge is `rag_search`,
and an operation performed by the application is `application_action`.

## Consequences

- The pinned 3B job makes 24 planner calls, so it remains bounded but takes
  longer than the old seven-case smoke gate.
- A release can fail even when aggregate accuracy passes; the report names the
  exact weak slice.
- Adding a language or planner kind requires adding a complete balanced slice,
  not one convenient example.
- The real model result remains an external measurement. Unit tests prove the
  arithmetic and fail-closed behaviour, not the model's quality.

## Verification

Measured on 2026-11-03 with the pinned
`Qwen2.5-3B-Instruct-Q4_K_M.gguf` artifact (SHA-256
`9c9f56a391a3abbd5b89d0245bf6106081bcc3173119d4229235dd9d23253f94`):

- aggregate kind accuracy: 23/24 (95.83%);
- model coverage: 24/24 (100%);
- all six slices pass; the weakest is `vi:direct` at 3/4 (75%);
- p95 planner latency on the measured Windows CPU run: 17.109 seconds.

The same snapshot passed 2,484 tests with 100% coverage over 10,463 statements
and 2,846 branches, plus 259/259 killed mutants.
