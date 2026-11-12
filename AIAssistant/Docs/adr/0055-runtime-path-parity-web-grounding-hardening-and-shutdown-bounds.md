# ADR 0055: Runtime path parity, web-grounding hardening, and shutdown bounds

## Context

An architectural review following ADR 0054 identified remaining cross-cutting
cracks across three operational boundaries:

1. **Deployment path & log parity**: `bootstrap/server.py` hardcoded logging
   under `PACKAGE_ROOT / "logs"` even when `APP_DATA_DIR` was set (breaking
   container `read_only: true` roots with `APP_DATA_DIR=/app/data` and diverging
   from `server_manager.LOGS_DIR` and `get_agent_logger()`), and passed
   `BASE_DIR` (`…/AIAssistant/src`) to `resolve_paths()` and `build_container()`
   so `tasks.sqlite` resolved to `AIAssistant/AIAssistant/` when `APP_DATA_DIR`
   was unset while `memory.sqlite` resolved to `<repo>/AIAssistant/`. In
   addition, `config/paths.py` wrote checkout logs outside `AIAssistant/`,
   `ChatbotAgentHolder` built/swapped the agent without a lock, and
   `server_manager.find_pid_on_port` matched port suffixes without requiring an
   exact port token.
2. **Web grounding integration gaps**: disabling web search (`AI_WEB_SEARCH_ENABLED=0`)
   still wired `HtmlWebSearchClient` into `ChatbotAgent` (running a blocking LLM
   freshness check on every chat query) and advertised `web_search` in the tool
   catalog. Default system prompt Rule 14 ("do not expose citations")
   contradicted `[UNTRUSTED WEB EVIDENCE]` URL instructions, character-like
   external queries ("Who is …") triggered `suppress_citations=True`, and
   `/v1/chat/completions` dropped `web_sources` from `x_meta`. Meanwhile,
   `tool_contract.py` left `web_search` at the 10 s default below
   `WebSearchSettings`'s 30 s cap, and `HtmlWebSearchClient` lacked `http(s)://`
   scheme validation and anti-bot challenge detection.
3. **Worker lifecycle & error containment**: `TaskService.submit()`/`resume()`
   wrote `SUBMITTED`/`WORKING` rows before `_start()` refused a closing service,
   and `_start()` leaked failed `thread.start()` threads in `_workers`.
   `ToolExecutionService.execute()` consumed single-use approval tokens in
   `_admit()` before `_run_bounded()` checked `_closing`, and had no worker
   count cap against repeated timed-out lingering threads.
   `_stream_langgraph_execution` caught only `Exception`, wedging `_sse_events`
   on `BaseException`. `uvicorn.Config` omitted `timeout_graceful_shutdown`.
   `_PII_PATTERNS` trailed `_EGRESS_ASSIGNMENT`, and `adapters/a2a_client.py`
   kept a stale inbound-tracing comment.

## Decision

1. **One root across logs, RAG paths, and SQLite stores**:
   `config/paths.resolve_paths()` routes checkout logs to `base / "logs"` and
   `APP_DATA_DIR` logs to `data / "logs"`. `bootstrap/server.py` resolves paths
   against `PACKAGE_ROOT` first, initializes `setup_logging(str(_paths.logs_dir))`,
   and passes `PACKAGE_ROOT` to `build_container()`. `ChatbotAgentHolder` guards
   `get()`/`rebuild()` with a lock, and `server_manager.find_pid_on_port`
   matches the exact port token after `:`.
2. **Coherent, fail-closed web grounding**:
   `_create_web_search_client()` returns `None` when `enabled` is false and
   `create_tool_registry(..., include_web_search=...)` omits disabled
   `web_search` from the composition root's catalog; `ChatbotAgent` skips
   freshness classification when `web_search` is absent or `.enabled` is false.
   Rule 14 separates private RAG labels from public `[UNTRUSTED WEB EVIDENCE]`
   URL citations; character-like queries with web results do not set
   `suppress_citations`, and JSON chat responses expose `web_sources` under
   `x_meta`. `web_search` carries `timeout_seconds=30` in `TOOL_CONTRACT_OVERRIDES`.
   `HtmlWebSearchClient` enforces `http://`/`https://` endpoints with netloc
   and raises `WebSearchChallengeError` when anti-bot challenge HTML is detected.
3. **Pre-commit shutdown gates, bounded workers, and SSE containment**:
   `TaskService._ensure_open()` refuses `submit()`/`resume()` before database
   writes and `_start()` discards failed threads in `except BaseException`.
   `ToolExecutionService` checks `_is_closing()` before `_admit()` (preserving
   single-use grants while keeping the race guard in `_run_bounded()`), enforces
   `max_concurrent_workers` (default 32), and rejects overflow with
   `tool_workers_exhausted`. SSE stream workers catch `BaseException` so
   `_sse_events` never blocks forever, and `uvicorn.Config` bounds shutdown with
   `timeout_graceful_shutdown=10`. `_PII_PATTERNS` aligns assignment length/charset
   with `_EGRESS_ASSIGNMENT`, and `adapters/a2a_client.py` updates its trace
   comment.

## Verification

- `tests/integration/test_composition_root_bootstrap.py` and
  `tests/unit/test_paths_guards.py` verify `APP_DATA_DIR` log parity, store
  co-location, and `AI_WEB_SEARCH_ENABLED=0` catalog/chat suppression.
- `tests/unit/test_web_search.py`, `test_tool_contract.py`, `test_prompts.py`,
  and `test_http_chat_routes.py` pin endpoint scheme validation, challenge
  detection, skipped freshness checks, preserved web URL citations, 30 s
  budget, and `x_meta.web_sources`.
- `tests/unit/test_task_lifecycle_guards.py`, `test_tool_gateway_guards.py`,
  `test_http_agent_routes.py`, `test_bootstrap_coverage_gaps.py`,
  `test_server_manager.py`, `test_inference.py`, and
  `tests/integration/test_main_entry_points.py` verify pre-commit close guards,
  preserved approval grants, worker-pool caps, `BaseException` stream safety,
  bounded uvicorn shutdown, exact port matching, and PII pattern alignment.
- Fourteen new mutants cover these decisions (`MIN_MUTANTS` raised from 286 to
  300, adding `tools/factory.py` and `tools/tool_contract.py` to
  `PINNED_DECISION_FILES`).

