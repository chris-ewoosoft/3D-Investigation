# ADR 0052: Web-grounded external knowledge

## Context

RAG indexes the project corpus and the model supplies stable learned
knowledge. Neither is an authority for current prices, news, schedules,
versions, nor for verifying whether a proposed product or event exists. The
previous planner vocabulary forced those questions into `direct`, allowing a
plausible but unsupported answer.

## Decision

Add `web_search` as a fourth planner kind and an ordinary read-only research
tool. It is distinct from `rag_search`: RAG remains project-internal, while
web search is public outbound egress.

The execution boundary is a `WebSearchPort`. The composition root injects an
HTML search adapter into both the agent catalogue and the normal chat
specialist. The agent planner chooses `web_search`; normal chat asks the
loaded model for a small `WEB_SEARCH`/`DIRECT` freshness decision. Neither
path uses product names or query-specific keyword tables.

Configuration is deployment-owned through `AI_WEB_SEARCH_ENABLED`,
`AI_WEB_SEARCH_ENDPOINT`, `AI_WEB_SEARCH_TIMEOUT`,
`AI_WEB_SEARCH_MAX_RESULTS`, and `AI_WEB_SEARCH_USER_AGENT`. The default
adapter posts only the minimal query and extracts title, snippet and source
URL; it does not fetch result pages.

Outbound queries carrying credential-shaped material are refused. PII is
scrubbed, HTTP redirects are not followed, the response is capped at 1 MB,
and provider errors degrade to an explicit unavailable result. Returned text
is labelled untrusted data in model context, with instructions to cite URLs
and distinguish official information from rumours or absence of evidence.

## Verification

`tests/unit/test_web_search.py` pins configuration, parsing, source URL
preservation, size bounds, privacy refusal, provider failures, planner
routing, normal-chat classification and untrusted-context labelling. Four
mutation cases pin credential refusal, response bounds, chat freshness
routing and the planner contract; `MIN_MUTANTS` moves from 269 to 273.
