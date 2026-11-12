# ADR 0051 — End-to-end authenticated A2A and observable HTTP boundaries

- Status: Accepted
- Date: 2026-11-06
- Supersedes: the incomplete outbound half of ADR 0048

## Context

ADR 0048 made every inbound privileged surface require a bearer token, but
the outbound A2A adapters still sent discovery and task requests without an
`Authorization` header. Two compliant AIAssistant instances therefore could
not communicate. The health router also grouped public probes with `/metrics`
and `/v1/models`, leaving the latter two outside the declared bearer boundary.

The operations guide queried `agent_requests_total` for HTTP codes even though
that counter records graph-node outcomes (`success`/`error`). Those alerts
could never observe 429, 503, or 5xx responses.

## Decision

1. The composition root reads one explicit A2A egress credential and injects
   it into both `RemoteAgentRegistry` discovery and `TrustedA2AClient`
   dispatch. `AI_A2A_OUTBOUND_TOKEN` is the distinct peer credential;
   `AI_A2A_TOKEN` is its documented single-tenant fallback. Missing egress
   credentials fail closed before a network call.
2. Every Agent Card fetch and task submission carries
   `Authorization: Bearer <token>`. An integration test connects the real
   outbound adapter to the real token-gated inbound router and exercises both
   discovery and submit.
3. `/health`, `/livez`, and `/readyz` remain public probes. `/metrics` uses the
   admin guard; `/v1/models` uses the chat/agent guard.
4. HTTP transport outcomes use a separate `agent_http_requests_total` counter
   labelled by bounded-cardinality method and the actual status code. Graph
   node outcomes remain in `agent_requests_total`.
5. Docker paths, auth examples, PromQL, the ADR index, and CI evidence must be
   executable contracts. The docs test checks every ADR link and derives the
   test/mutation counts instead of only checking shared literal strings.

## Consequences

- A multi-tenant receiver never reuses its inbound token-to-tenant mapping as
  an egress secret. Operators configure the peer credential separately.
- Metrics and model inventory are no longer anonymously enumerable.
- Existing manual clients must send the documented bearer header.
- Eight transport/documentation decisions and the HTTP status vocabulary are
  pinned by new mutants; the mutation floor is 269.

## Verification

- `tests.unit.test_a2a_http_router.AuthenticatedPeerIntegrationTests`
- `tests.unit.test_a2a_client`
- `tests.unit.test_a2a_protocol_guards`
- `tests.unit.test_http_health_routes`
- `tests.unit.test_http_security.HttpMetricsMiddlewareTests`
- `tests.unit.test_observability`
- `tests.unit.test_docs_contract`
- `scripts/mutation_check.py` (`MIN_MUTANTS = 269`)
