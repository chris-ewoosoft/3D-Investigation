"""Mutation check for the load-bearing guards (ADR 0015 / 0011 / 0018 / 0020).

100% statement/branch coverage says the guards are *executed*, not that they
are *correct*: a test that asserts the wrong direction still covers the line.
This script inverts one decision at a time in each guard and runs the tests
that own it. A mutant is KILLED when the suite fails (the guard's behaviour is
pinned) and SURVIVES when it stays green (the test would not notice the guard
being disabled).

Mutants run in disposable sandbox copies, never in the working tree. Independent
sandboxes may run in parallel, and each owning suite is first run unmodified so
an already-red test cannot be misreported as a killed mutant. A PID lock keeps
two resource-heavy sweeps from overlapping and automatically recovers when the
previous owner no longer exists.

An anchor that no longer matches the source counts as STALE and fails the run:
the check may not go green by mutating code that is not there.

Usage (from AIAssistant/):
  PYTHONPATH=src python scripts/mutation_check.py --jobs 2
  PYTHONPATH=src python scripts/mutation_check.py --files src/ai_assistant/observability/tracing.py
Exit code 0 = every mutant killed and every anchor present; 1 otherwise.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

BASE = Path(__file__).resolve().parent.parent
REPOSITORY_ROOT = BASE.parent
DEFAULT_JOBS = 2

_SANDBOX_IGNORED_NAMES = frozenset(
    {
        ".coverage",
        ".deepeval",
        ".grimp_cache",
        ".import_linter_cache",
        ".mypy_cache",
        ".mutation_check.lock",
        ".pytest_cache",
        ".ruff_cache",
        "AIAssistant",  # checkout-local SQLite state directory
        "Cache",
        "Models",
        "__pycache__",
        "logs",
    }
)

# Contract tests and tool-catalog loading intentionally read these outer
# repository files relative to the repository root. Keeping these tiny authored
# files beside each sandbox means every owning suite works without copying build
# output or model artifacts.
_OUTER_CONTRACT_FILES = (
    ".github/workflows/agent-evals.yml",
    "Config/agent_action_manifest.json",
    "Config/agent_tools.json",
    "src/core/AppConfig.cpp",
    "src/core/AtRestCipher.cpp",
    "tests/TestAtRestCipher.cpp",
)


@dataclass(frozen=True)
class Mutant:
    """One deliberate logic inversion in one guard, with the tests that own it."""

    file: str
    original: str
    mutated: str
    tests: tuple[str, ...]
    label: str
    # Per-mutant budget (seconds) for the owning suite. The default fits every
    # suite that terminates; a mutant may only shorten it, never extend it.
    timeout_seconds: int = 600
    # Opt-in, one mutant only (see the recompose guard below): when breaking
    # the guard turns the owning suite from "failing" into "never finishing",
    # a timeout IS the detection — but only with a budget far above the
    # unmutated runtime, so a slow machine can never fake a kill.
    kill_on_timeout: bool = False


#: The number of guards this gate is contractually required to cover.
#:
#: Without a floor, *deleting* a mutant is indistinguishable from *passing*
#: it: the suite stays green, the coverage floor stays at 100 %, and the
#: guard silently stops being guarded. ADR 0032 makes the count a gate — a
#: new load-bearing decision adds a mutant in the same commit, and a removed
#: one lowers this number in the same commit, so the diff always shows it.
#:
#: Measured 2026-10-20: 67 mutants, 67 killed, 0 survived, 0 stale.
#: ADR 0032 adds seven more (the policy-tree write deny and its prefix match,
#: the OpenAPI surface, the log redaction, the compare-and-set, the
#: checkpoint seal, the adapter's CAS guard and the volatile-key rule).
#: ADR 0033 adds one more (the write target is resolved, so a symlink into a
#: policy tree is matched on the file it points at).
#: ADR 0034 adds five more (the secret-file read guard, the PATH scan that
#: never consults the current directory, the case-insensitive write deny,
#: the sealed-regime read refusal and the value-shaped egress check).
#: ADR 0035 adds six more (the path-shaped program name, the case-insensitive
#: blocked-directory rule, the path confinement itself, the LSP server
#: resolution, the fail-closed git resolution and the attachment gate).
#: The command-surface hardening adds eleven more (the pre-verb option
#: allow-list, the git destination confinement, the attached git config
#: spelling, the pytest plugin/import options, the ctest option allow-list,
#: the two write deny-tree guards, the classification comparison, the
#: clearance derivation, the unknown classification label and the isolation
#: gate's default).
#: The row-bound at-rest state adds three more (the row-bound associated
#: data, the identity verification and the refusal of a state file that
#: predates it).
#: The single-rule cleanup adds four more (the success rule, the cursor
#: detection, the cursor use and the regular-file gate on the attachment).
#: The corpus boundary adds five more (the link rule, the secret names, the
#: shared blocked directories, the extension lists and the cache hash).
#: The sixth review adds one more (the sealing action refuses a state that is
#: already sealed).
#: The seventh review adds one more (the binaries whose path positionals are
#: confined).
#: The eighth review adds four more: one model lease per entry point that
#: runs the graph (``execute``, ``approve``, the desktop ack) plus the chat
#: route's inference span (ADR 0040).
#: The tenth review adds ten more: the owner-enforced reload window (guard and
#: the startup caller), the fail-closed scope map, the missing-policy and
#: missing-catalogue bootstrap refusals, the graceful stop (event, watcher and
#: admin route) and the public RAG surface (property and health read).
#: ADR 0043 adds four more: the no-overlap recall drop, the memory-kind runtime
#: gate, the bounded trim window and the over-budget compaction shrink.
#: The short-term buffer adds seven more: the window rule, the merge that keeps
#: the remembered context, the empty-record no-op, the session-map eviction,
#: the facade's read (merge), its write (record) and the answer turn it skips
#: when the run never concluded.
#: The long-term memory loop adds fifteen more (ADR 0043 §3-§5): the recall's
#: fail-closed read (a throwing store), the kind re-check at the trust crossing,
#: the redaction gate that drops a still-sensitive record, the contained write,
#: the inert-without-store ``enabled``; the store's required key, its unknown-
#: kind refusal on the way in, the sealed-identity re-check on the way out and
#: the byte/age retention rules; the per-run semantic cap and the plan-only
#: procedural record; and the facade's three wiring decisions (recall before a
#: run, consolidate a completed run only, never announce an empty recall).
#: The procedural seed adds thirteen more (ADR 0043 §7): the lookup's own
#: fail-closed half (off switch, throwing store, wrong kind, wrong signature,
#: non-mapping metadata, non-string text, text with no steps), the seed's
#: decisions in the plan node (reuse instead of planning, mark it verified,
#: count the attempt) and the four hops that carry ``prior_plan`` from the
#: facade to the state (service, context, runner, graph.run).
#: The parallel tool batch adds twenty-two more (ADR 0044): the group
#: eligibility rules (approval-free, rag-only head and members, width), the
#: parser's four fail-closed group checks and the checkpoint re-check, the
#: fan-out itself (the per-branch cancel gate, the join, the delegation
#: precompute and its cache lock), the merge (ordered pairs, the head's
#: position, the sibling evidence, the group-wide call budget), the error
#: streak reset, the fan-out bounds, the evidence-aware step directive and the
#: planner schema that now declares the contract the prompt asks for.
#: The lazy-encoder load adds two more (ADR 0044 §9): the lock that makes
#: "load once" a guarantee when two ``rag_search`` branches hit a cold cache,
#: and the double-check that stops the waiting branches from repeating a
#: completed load.
#: The tampered-column skip adds one more (ADR 0043/0036): a non-numeric
#: column drops its own row instead of raising out of ``candidates`` and
#: taking every healthy row with it.
#: Two more guards from the same follow-up: the ``Referer``-to-origin
#: normalisation (a full URL must not be refused by an origin-only list) and
#: the contained memory-store close (one failing handle must not skip the
#: other on shutdown).
#: Consolidation on resume adds six more (ADR 0043 §10): the parked action
#: carries the run's owner, the shared context consolidates only completed
#: runs and forwards that owner, a chained desktop action keeps the owner,
#: and the two non-graph completion fallbacks still consolidate.
#: The checkpoint-lifecycle follow-up adds seven more: the Postgres/Redis
#: factory context manager is entered (not called and discarded), a failing
#: setup releases it, a bare relative SQLite path does not call makedirs(""),
#: the handle's close is idempotent, a malformed memory record cannot fail
#: the run, and the lifespan closes the handle (and contains a failing close).
#: ADR 0047 adds six more: the closed side-effect vocabulary at construction,
#: the per-owner byte budget (and its empty-store guard), the per-owner
#: session eviction, the facade forwarding that owner, and the desktop ack's
#: session binding.
#: ADR 0055 adds fourteen more: APP_DATA_DIR log unification and disabled
#: web_search wiring, HTTP(S) endpoint validation and bot-challenge detection,
#: skipping freshness inference and preserving URLs on enabled/character queries,
#: excluding disabled web_search from the tool catalog with a 30s timeout budget,
#: refusing task submit/resume before committing while closing, preserving
#: single-use grants on close and capping lingering tool workers, containing
#: BaseException in SSE stream workers, and bounding uvicorn graceful shutdown.
MIN_MUTANTS = 308

MUTANTS: tuple[Mutant, ...] = (
    # ── Sandbox allow-list (ADR 0011) ────────────────────────────────────────
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if executable.casefold() not in _ALLOWED:",
        "if False:",
        ("tests.unit.test_sandbox",),
        "executable allow-list removed (any binary runs)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        'if "/" in executable or "\\\\" in executable:',
        "if False:",
        ("tests.unit.test_sandbox",),
        "path-shaped executable accepted (/opt/attacker/git runs as git)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        'return "Sandbox: dangerous cmake -E subcommand blocked."',
        "return None",
        ("tests.unit.test_sandbox",),
        "cmake -E <arbitrary cmd> allowed",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        'return "Sandbox: git -c config injection blocked."',
        "return None",
        ("tests.unit.test_sandbox",),
        "git -c core.sshCommand injection allowed",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "return f\"Sandbox: subcommand '{token}' is not in the {executable} allow-list.\"",
        "return None",
        ("tests.unit.test_sandbox",),
        "unknown subcommand allowed",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if head in _GIT_ESCAPE_FLAGS or argument == \"-c\" or argument.startswith(\"-c\"):",
        "if False:",
        ("tests.unit.test_sandbox",),
        "git escape flags (--upload-pack/--exec-path/…) allowed",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if verb is not None and verb not in _GIT_BISECT_VERBS:",
        "if False:",
        ("tests.unit.test_sandbox",),
        "git bisect run (arbitrary verdict hook) allowed",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if not value or _path_escapes_workspace(value, cwd):",
        "if False:",
        ("tests.unit.test_sandbox",),
        "cmake -P/--build pointed outside the workspace allowed",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if resolved is None:",
        "if False:",
        ("tests.unit.test_sandbox",),
        "binary absent from PATH delegated to the OS search (cwd hijack)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if target_name.lower() in _DENIED_WRITE_NAMES or any(",
        "if False:",
        ("tests.unit.test_sandbox",),
        "write-deny on .env/policy files removed",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if target_name.lower() in _DENIED_WRITE_NAMES or any(",
        "if target_name in _DENIED_WRITE_NAMES or any(",
        ("tests.unit.test_sandbox",),
        "the write deny-list compared case-sensitively (PYPROJECT.TOML "
        "replaces pyproject.toml on a case-insensitive filesystem)",
    ),
    Mutant(
        "src/ai_assistant/exec_policy.py",
        'for entry in (path or "").split(os.pathsep):',
        'for entry in (path or "").split(os.pathsep) + [os.curdir]:',
        ("tests.unit.test_sandbox",),
        "the PATH scan consults the current directory (a planted binary wins "
        "over the operator's own tool)",
    ),
    Mutant(
        "src/ai_assistant/exec_policy.py",
        "if not name or os.path.isabs(name) or os.path.dirname(name):",
        "if False:",
        ("tests.unit.test_sandbox",),
        "a path-shaped program name is resolved instead of refused (the "
        "allow-list constrains the name, not the path)",
    ),
    Mutant(
        "src/ai_assistant/path_policy.py",
        "    return name.lower() in _BLOCKED_DIR_NAMES",
        "    return name in _BLOCKED_DIR_NAMES",
        ("tests.unit.test_path_policy",),
        "the blocked-directory rule compares case-sensitively (.GIT is "
        "the same directory as .git on a case-insensitive filesystem)",
    ),
    Mutant(
        "src/ai_assistant/path_policy.py",
        "        canonical_path(target).relative_to(canonical_path(root_path))",
        "        pass",
        ("tests.unit.test_path_policy",),
        "the path confinement check removed (a junction out of the root is "
        "read as if it were inside it)",
    ),    Mutant(
        "src/ai_assistant/path_policy.py",
        "        if os.path.commonpath([lexical, lexical_root]) != lexical_root:",
        "        if False:",
        ("tests.unit.test_path_policy",),
        "the spelling half of the confinement check removed (a path that "
        "only looks like it leaves the root is accepted)",
    ),
    Mutant(
        "src/ai_assistant/tools/lsp_client.py",
        "    resolved_bin = resolve_tool_binary(server_bin)",
        "    resolved_bin = shutil.which(server_bin)",
        ("tests.unit.test_lsp_client",),
        "the LSP server is resolved with the platform lookup again (the "
        "current directory wins over PATH on Windows)",
    ),
    Mutant(
        "src/ai_assistant/tools/builtin/vcs_tools.py",
        '    binary = resolve_executable("git", os.environ.get("PATH", ""))',
        '    binary = "git"',
        ("tests.unit.test_vcs_tools",),
        "the git tools fall back to a bare name (the OS searches the "
        "current directory first on Windows)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/chat_routes.py",
        "        if resolve_within(root, path) is not None:",
        "        if False:",
        ("tests.unit.test_http_chat_routes",),
        "a client-named attachment is opened from anywhere on the host",
    ),
    Mutant(
        "src/ai_assistant/tools/tool_contract.py",
        "    if override is None and canonical not in READ_ONLY_TOOLS:",
        "    if False:",
        ("tests.unit.test_tool_contract",),
        "an unknown tool silently receives the read-only default instead of "
        "failing bootstrap (the review's unlisted-tool finding)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "    if _denied_tree_hit(relative):\n"
        '        return {"error": "Write target is denied by sandbox policy (policy tree)."}',
        "    if False:\n"
        '        return {"error": "Write target is denied by sandbox policy (policy tree)."}',
        ("tests.unit.test_sandbox",),
        "write-deny on the policy trees removed (the agent may rewrite the "
        "transport policy, the A2A scope map and the approval logic)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "        if parts[:len(denied_parts)] == denied_parts:",
        "        if False:",
        ("tests.unit.test_sandbox",),
        "the policy-tree match compares the wrong prefix (every policy file "
        "becomes writable again)",
    ),
    Mutant(
        "src/ai_assistant/domain/approvals.py",
        "    if ignore_volatile:",
        "    if False:",
        ("tests.unit.test_reason_node_guards", "tests.unit.test_approval_grants"),
        "the volatile per-retry keys are no longer filtered out of the retry "
        "fingerprint (a retry that only changed its request_id slips past the "
        "duplicate-call guard)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/app_factory.py",
        '        docs_url="/docs" if docs else None,\n'
        '        redoc_url="/redoc" if docs else None,\n'
        '        openapi_url="/openapi.json" if docs else None,',
        '        docs_url="/docs",\n'
        '        redoc_url="/redoc",\n'
        '        openapi_url="/openapi.json",',
        ("tests.unit.test_final_edges_b", "tests.integration.test_real_http_surface"),
        "the OpenAPI surface is published even when the deployment did not "
        "opt in (it is an app-level route, so the transport guard never "
        "reaches it)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        '        summary = describe_tool_result(result)',
        "        summary = str(result)",
        ("tests.unit.test_tool_node_guards", "tests.unit.test_tool_result_logging"),
        "the tool payload is written to the plaintext log again",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "                if cursor.rowcount != 1:",
        "                if False:",
        ("tests.unit.test_final_edges_a", "tests.unit.test_task_service_cas_races"),
        "a lost compare-and-set still writes the event (the log announces a "
        "transition that did not happen)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        "    if cipher is None:\n        return None",
        "    if True:\n        return None",
        ("tests.unit.test_checkpointing",),
        "the LangGraph checkpoint store is written in the clear (the "
        "conversation is a plaintext copy of what the task store seals)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        '_FORBIDDEN_METACHAR = {"&&", "||", ";", "|", ">", "<", "`", "$(", "${"}',
        '_FORBIDDEN_METACHAR = {"&&", "||", ";", "|", ">", "<", "`", "$("}',
        ("tests.unit.test_sandbox",),
        "${VAR} expansion allowed (shell expansion behind the metachar gate)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if _path_escapes_workspace(argument, cwd):\n            return f\"Sandbox: {executable} path must stay inside the workspace.\"",
        "if False:\n            return f\"Sandbox: {executable} path must stay inside the workspace.\"",
        ("tests.unit.test_sandbox",),
        "a path positional outside the workspace is allowed (ruff check <dir> "
        "prints the source lines of every file it finds)",
    ),
    # ── HITL approval gate ───────────────────────────────────────────────────
    Mutant(
        "src/ai_assistant/application/tools.py",
        "if spec.requires_approval and not self._grants.covers(",
        "if False and not self._grants.covers(",
        ("tests.unit.test_tool_gateway_guards", "tests.unit.test_deterministic_orchestrator"),
        "approval-required tools run without approval",
    ),
    Mutant(
        "src/ai_assistant/domain/governance.py",
        "if isinstance(value, str):\n        return scrub_pii(value)",
        "if False:\n        return scrub_pii(value)",
        ("tests.unit.test_governance_egress", "tests.unit.test_tool_event_wiring"),
        "secret pasted under a plain key reaches logs unmasked",
    ),
    # ── Contract: Qt history bound ───────────────────────────────────────────
    Mutant(
        "src/ai_assistant/agents/models.py",
        "history:             list[dict[str, Any]] = Field(default_factory=list, max_length=200)",
        "history:             list[dict[str, Any]] = Field(default_factory=list, max_length=20)",
        ("tests.contract.test_agent_contract_qt",),
        "history bound regressed 200 -> 20 (long sessions 422 again)",
    ),
    # ── Remote-leg opt-in (ADR 0018) ─────────────────────────────────────────
    Mutant(
        "src/ai_assistant/settings.py",
        'return values.get("AGENT_HYBRID_POLICY", "local_only").casefold()',
        'return values.get("AGENT_HYBRID_POLICY", "cloud_allowed").casefold()',
        ("tests.unit.test_inference",),
        "default hybrid policy flipped to cloud_allowed",
    ),
    # ── /health honesty ──────────────────────────────────────────────────────
    Mutant(
        "src/ai_assistant/adapters/http/health_routes.py",
        'status = "not_ready" if unready else ("degraded" if reasons else "ready")',
        'status = "ready"',
        ("tests.unit.test_http_health_routes",),
        "/health always claims ready",
    ),
    # ── RAG cache seal fail-closed (ADR 0028) ─────────────────────────────────
    # The cache holds a second copy of the user's document corpus, so it must
    # be sealed on disk and never loaded without being authenticated.
    Mutant(
        "src/ai_assistant/rag/index.py",
        "        path.write_bytes(cipher.seal_bytes(payload, context=context))",
        "        path.write_bytes(payload)",
        ("tests.unit.test_index",),
        "the cache artifacts are written to disk in the clear",
    ),
    Mutant(
        "src/ai_assistant/rag/index.py",
        "        if not is_sealed_metadata(meta):",
        "        if False:",
        ("tests.unit.test_index",),
        "a cache that was never sealed is loaded instead of rebuilt",
    ),
    Mutant(
        "src/ai_assistant/rag/index.py",
        '        "sealed": seal_metadata(),',
        '        "sealed": {},',
        ("tests.unit.test_index",),
        "the seal marker is not recorded (the cache can never be a hit)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/storage_crypto.py",
        "            return self._aead.decrypt(\n"
        "                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _associated_data(context),\n"
        "            )\n"
        "        except (InvalidTag, ValueError) as error:",
        "            return self._aead.decrypt(\n"
        "                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _associated_data(context),\n"
        "            )\n"
        "        except ValueError as error:",
        ("tests.unit.test_storage_crypto",),
        "a tampered binary envelope escapes as a raw crypto error",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/storage_crypto.py",
        "        if not is_sealed_bytes(value):\n"
        '            message = f"{context} is not a sealed envelope"\n'
        "            raise StorageDecryptionError(message)",
        "        if not is_sealed_bytes(value):\n"
        "            return value",
        ("tests.unit.test_index", "tests.unit.test_storage_crypto"),
        "an unsealed binary artifact is accepted as plaintext (legacy cache loaded)",
    ),
    # ── Cancel that races the worker ─────────────────────────────────────────
    Mutant(
        "src/ai_assistant/application/coordination.py",
        'if previous is not None and previous.cancelled and previous.metadata.get("pending_cancel"):',
        "if False:",
        ("tests.unit.test_task_coordinator_lifecycle",),
        "pending cancel lost when the worker registers its run",
    ),
    Mutant(
        "src/ai_assistant/application/coordination.py",
        "if record.status in self._TERMINAL and not record.cancelled:",
        "if False:",
        ("tests.unit.test_task_coordinator_lifecycle",),
        "cancelling a finished run resurrects it",
    ),
    # ── Transport policy: loopback is routing, never authentication (ADR 0048)
    Mutant(
        "src/ai_assistant/bootstrap/server_manager.py",
        '    if admin_token:\n        headers["Authorization"] = f"Bearer {admin_token}"',
        '    if False:\n        headers["Authorization"] = f"Bearer {admin_token}"',
        ("tests.unit.test_server_manager",),
        "server_manager omits the Authorization header on /admin/shutdown (ADR 0049)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/server_manager.py",
        "            if exit_code is None:",
        "            if exit_code is not None:",
        ("tests.unit.test_server_manager",),
        "an early child crash is falsely reported as a 600-second startup timeout",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/security.py",
        "return not policy.mcp_token",
        "return False",
        ("tests.unit.test_http_security",),
        "an MCP surface without AI_MCP_TOKEN is mounted anonymously",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/security.py",
        "if not has_token:\n        _reject(503, f\"{surface} requires a configured access token\")",
        "if False:\n        _reject(503, f\"{surface} requires a configured access token\")",
        ("tests.unit.test_http_security",),
        "a privileged surface accepts requests without any configured credential",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/security.py",
        "else:\n        _reject(503, \"a2a requires a configured access token\")",
        "else:\n        tenant = \"default\"",
        ("tests.unit.test_http_security",),
        "anonymous A2A requests regain the shared default tenant",
    ),
    # ── Classification survives persistence (ADR 0010) ───────────────────────
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "classification=DataClassification(classification),",
        "classification=DataClassification.INTERNAL,",
        ("tests.unit.test_final_edges_a",),
        "reload promotes a RESTRICTED task back to INTERNAL",
    ),
    # ── Remote egress refuses sensitive params (ADR 0002/0010) ───────────────
    Mutant(
        "src/ai_assistant/agents/graph_wiring.py",
        "if has_sensitive_content(params):",
        "if False:",
        ("tests.unit.test_runner",),
        "credential-bearing params dispatched to a remote specialist",
    ),
    # ── Step vocabulary trust boundary (ADR 0019) ────────────────────────────
    Mutant(
        "src/ai_assistant/domain/steps.py",
        "if kind not in get_args(StepType):",
        "if False:",
        ("tests.unit.test_step_boundary_validation",),
        "step vocabulary check removed (unknown discriminator accepted)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/pending_store.py",
        'if "steps" in value:',
        "if False:",
        ("tests.unit.test_step_boundary_validation",),
        "persisted step log accepted without the runtime gate",
    ),
    Mutant(
        "src/ai_assistant/adapters/orchestration/deterministic.py",
        "validated = validate_steps(steps)",
        "validated = list(steps)",
        ("tests.unit.test_step_boundary_validation",),
        "continuation steps accepted without the vocabulary gate",
    ),
    # ── Plugin enablement gate: metadata before module execution ─────────────
    Mutant(
        "src/ai_assistant/plugins/loader.py",
        "if not any(registry.allows(name) for name in _declared_names(entry_point)):",
        "if False:",
        ("tests.unit.test_plugins", "tests.unit.test_plugin_reentry"),
        "disabled plugin instantiated (its module code runs at bootstrap)",
    ),
    # ── Approval resume re-enters the session run lock ───────────────────────
    Mutant(
        "src/ai_assistant/agents/service.py",
        "        with self._hold_session(request.session_id):\n"
        "            slots = self._acquire_run_slot()\n"
        "            try:\n"
        "                with self._model_lease():\n"
        "                    return self._approval_service.approve(request)\n"
        "            finally:\n"
        "                self._release_run_slot(slots)",
        "        slots = self._acquire_run_slot()\n"
        "        try:\n"
        "            with self._model_lease():\n"
        "                return self._approval_service.approve(request)\n"
        "        finally:\n"
        "            self._release_run_slot(slots)",
        ("tests.unit.test_session_serialization",),
        "approval resume runs outside the session lock (interleaves a run)",
    ),
    # ── Transport hardening: bounded, redirect-free A2A egress ───────────────
    Mutant(
        "src/ai_assistant/adapters/a2a_client.py",
        "if len(body) > _MAX_RESPONSE_BYTES:",
        "if False:",
        ("tests.unit.test_a2a_client",),
        "unbounded remote response buffered whole (memory DoS)",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_client.py",
        "def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:\n        # The parameters are the handler contract; none of them are read here.\n        return None",
        "def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:\n        return super().redirect_request(req, fp, code, msg, headers, newurl)",
        ("tests.unit.test_a2a_client", "tests.unit.test_a2a_protocol_guards"),
        "A2A redirects followed (SSRF behind the endpoint allowlist) — on the "
        "dispatch leg and on the discovery leg, which share one opener",
    ),
    Mutant(
        "src/ai_assistant/settings.py",
        '        or values.get("AI_A2A_TOKEN", "").strip()\n',
        '        or ""\n',
        ("tests.unit.test_settings",),
        "the documented single-token A2A configuration is not propagated to egress",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_client.py",
        "        if not self._bearer_token:\n"
        "            return {\"routing\": \"rejected_policy\", \"error\": \"A2A outbound bearer token is not configured\"}",
        "        if False:\n"
        "            return {\"routing\": \"rejected_policy\", \"error\": \"A2A outbound bearer token is not configured\"}",
        ("tests.unit.test_a2a_client",),
        "an outbound A2A call without a credential reaches the wire",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_client.py",
        '            "Authorization": f"Bearer {self._bearer_token}",',
        '            "Authorization": "Bearer",',
        ("tests.unit.test_a2a_client", "tests.unit.test_a2a_http_router"),
        "A2A dispatch omits the configured bearer credential",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_protocol.py",
        "        if not self._bearer_token:\n"
        "            logger.warning(\"A2A discovery skipped: outbound bearer token is not configured\")\n"
        "            return self.snapshot()",
        "        if False:\n"
        "            logger.warning(\"A2A discovery skipped: outbound bearer token is not configured\")\n"
        "            return self.snapshot()",
        ("tests.unit.test_a2a_protocol_guards",),
        "A2A discovery without a credential reaches the wire",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_protocol.py",
        '                    "Authorization": f"Bearer {self._bearer_token}",',
        '                    "Authorization": "Bearer",',
        ("tests.unit.test_a2a_protocol_guards",),
        "A2A discovery omits the configured bearer credential",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/health_routes.py",
        '@router.get("/metrics", dependencies=admin_guards(resolved))',
        '@router.get("/metrics")',
        ("tests.unit.test_http_health_routes",),
        "the metrics surface is public despite the privileged-surface contract",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/health_routes.py",
        '@router.get("/v1/models", dependencies=chat_guards(resolved))',
        '@router.get("/v1/models")',
        ("tests.unit.test_http_health_routes",),
        "the model inventory is public despite the privileged-surface contract",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/app_factory.py",
        '                    int(message["status"]),',
        "                    200,",
        ("tests.unit.test_http_security",),
        "HTTP metrics report every response as 200, hiding 429/503/5xx alerts",
    ),
    Mutant(
        "src/ai_assistant/observability/metrics.py",
        '        metrics["http_requests"].labels(method.upper(), str(status_code)).inc()',
        '        metrics["http_requests"].labels(method.upper(), "success").inc()',
        ("tests.unit.test_observability",),
        "the HTTP counter reuses node outcomes instead of real status codes",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_protocol.py",
        "if not isinstance(base_url, str) or not _is_http_url(base_url):",
        "if False:",
        ("tests.unit.test_a2a_protocol_guards",),
        "non-HTTP discovery URL fetched (SSRF via discovery)",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_protocol.py",
        "if len(raw) > _DISCOVERY_MAX_BYTES:",
        "if False:",
        ("tests.unit.test_a2a_protocol_guards",),
        "oversized Agent Card buffered whole (memory DoS via discovery)",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_protocol.py",
        "                        url=base_url, card=card_data, skills=skills,",
        '                        url=str(card_data.get("url") or base_url), card=card_data, skills=skills,',
        ("tests.unit.test_a2a_protocol_guards",),
        "Agent Card controls the dispatch endpoint (allowlist bypass)",
    ),
    Mutant(
        "src/ai_assistant/tools/builtin/path_utils.py",
        "if is_secret_name(os.path.basename(rel_path)) or is_secret_name(real_basename):",
        "if False:",
        ("tests.unit.test_file_tools",),
        "the secret-file read guard removed (the agent reads .env — the "
        "at-rest key and every bearer token — without approval)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "if require_sealed and not cipher.is_sealed(value):",
        "if False:",
        ("tests.unit.test_sqlite_store_crypto",),
        "an unsealed column served as authentic (AEAD downgrade: a "
        "substituted plaintext column is accepted instead of rejected)",
    ),
    Mutant(
        "src/ai_assistant/domain/governance.py",
        "    if isinstance(value, str):\n        return carries_credential_shape(value)",
        "    if False:\n        return carries_credential_shape(value)",
        ("tests.unit.test_governance_egress",),
        "a credential pasted into a free-text value is not detected (the "
        "user's own prompt leaves the trust boundary in clear)",
    ),
    Mutant(
        "src/ai_assistant/tools/builtin/vcs_tools.py",
        "if sub_rejection:",
        "if False:",
        ("tests.unit.test_vcs_tools",),
        "blocked git subcommand runs outside the sandbox gate",
    ),
    # ── Task-list pagination is clamped, never handed to SQL raw ─────────────
    Mutant(
        "src/ai_assistant/adapters/a2a_router.py",
        "return max(1, min(int(value), _MAX_TASK_PAGE))",
        "return int(value)",
        ("tests.unit.test_a2a_http_router",),
        "limit=-1 reaches SQLite as LIMIT -1 (whole task table)",
    ),
    # ── Request-body cap refuses before parsing ──────────────────────────────
    Mutant(
        "src/ai_assistant/bootstrap/app_factory.py",
        "if declared > self._max_bytes:",
        "if False:",
        ("tests.unit.test_http_security",),
        "declared oversize body accepted (no 413)",
    ),
    # ── Backend owns the llama-context serialization ─────────────────────────
    Mutant(
        "src/ai_assistant/llm/llama_cpp_backend.py",
        "with self._generation_lock:\n            return self._llm.create_chat_completion(",
        "if True:\n            return self._llm.create_chat_completion(",
        ("tests.unit.test_llm_backend",),
        "llama context no longer serialized (overlapping generations corrupt it)",
    ),
    # ── Lazy composition root (ADR 0020) ──────────────────────────────────────
    Mutant(
        "src/ai_assistant/bootstrap/server.py",
        "if name in _COMPOSED_NAMES:",
        "if False:",
        ("tests.integration.test_composition_root_bootstrap",),
        "__getattr__ never composes (composed names degrade to AttributeError)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/__init__.py",
        "raise AttributeError(msg) from None",
        "return None",
        ("tests.integration.test_composition_root_bootstrap",),
        "unknown bootstrap attribute returns None instead of raising",
    ),
    # Without the early return, the compose() → _register_optional_protocols()
    # → compose() back-edge never terminates: every cycle re-opens logs and
    # stores (~1.6s of IO), so the owning suite hangs instead of failing
    # (observed >600s unmutated-vs-mutated). The file runs ~25s unmutated, so
    # a 180s budget with kill_on_timeout cannot fake a kill on a slow
    # machine — a timeout here IS the guard being load-bearing.
    Mutant(
        "src/ai_assistant/bootstrap/server.py",
        "if _COMPOSED:\n"
        "            return\n"
        "        # Set before the body so the compose() → _register_optional_protocols()\n"
        "        # → compose() back-edge terminates instead of rebuilding. The public\n"
        "        # wrapper resets it if the body raises.\n"
        "        _COMPOSED = True",
        "_COMPOSED = True",
        ("tests.integration.test_composition_root_bootstrap",),
        "second compose() rebuilds the graph (duplicate handlers and routers)",
        timeout_seconds=180,
        kill_on_timeout=True,
    ),
    Mutant(
        "src/ai_assistant/bootstrap/server.py",
        "except BaseException:\n        _COMPOSED = False\n        raise",
        "except BaseException:\n        raise",
        ("tests.integration.test_composition_root_bootstrap",),
        "a failed compose leaves the flag set (half-built graph looks ready)",
    ),
    # ── A2A per-tenant task ownership ─────────────────────────────────────────
    Mutant(
        "src/ai_assistant/adapters/http/security.py",
        "if tenant is None:",
        "if False:",
        ("tests.unit.test_http_security", "tests.unit.test_a2a_http_router"),
        "an unknown A2A tenant token is accepted instead of 401",
    ),
    Mutant(
        "src/ai_assistant/application/tasks.py",
        "def get(self, task_id: str, *, owner: str = \"default\") -> AgentTask | None:\n"
        "        task = self._store.get(task_id)\n"
        "        if task is None or task.owner != owner:",
        "def get(self, task_id: str, *, owner: str = \"default\") -> AgentTask | None:\n"
        "        task = self._store.get(task_id)\n"
        "        if task is None:",
        ("tests.unit.test_task_lifecycle_guards", "tests.unit.test_a2a_http_router"),
        "a task can be read across tenant boundaries",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "if context_id is not None and owner is not None:",
        "if False:",
        ("tests.unit.test_final_edges_a",),
        "the owner filter is dropped when listing tasks",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "if retention_days <= 0:",
        "if False:",
        ("tests.unit.test_final_edges_a",),
        "a disabled retention window still prunes tasks",
    ),
    # ── Production gates & admission governor (ADR 0023/0024) ────────────────
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        'if runtime_settings.sandbox_require_isolation and runtime_settings.sandbox_runtime != "docker":',
        "if False:",
        ("tests.unit.test_sandbox",),
        "isolation gate removed (local runtime runs with server privileges)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        'if required and backend == "memory":',
        "if False:",
        ("tests.unit.test_checkpointing",),
        "required-checkpoint gate removed (silent MemorySaver fallback)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        "        if checkpoint_time is None:\n            unverifiable.add(thread_id)",
        "        if False:\n            unverifiable.add(thread_id)",
        ("tests.unit.test_checkpointing",),
        "a thread containing a legacy checkpoint id is aged from incomplete evidence and deleted",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        "        if thread_id not in unverifiable and latest < cutoff_unix",
        "        if thread_id not in unverifiable and latest >= cutoff_unix",
        ("tests.unit.test_checkpointing",),
        "checkpoint retention deletes active threads and preserves expired ones",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        "if handle is not None:\n            return handle\n    except Exception as error:",
        "if handle is not None:\n            return handle\n    except KeyboardInterrupt as error:",
        ("tests.unit.test_checkpointing",),
        "unexpected backend failure escapes before the gate decides",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/rate_limit.py",
        "if used >= self._limit:",
        "if False:",
        ("tests.unit.test_rate_limit",),
        "per-peer budget never refuses (rate limit is decorative)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "if slots is not None and not slots.acquire(blocking=False):",
        "if False:",
        ("tests.unit.test_session_serialization",),
        "run budget removed (unbounded concurrent runs)",
    ),
    # ADR 0040 pinned the model lease to every entry point that runs the
    # graph; ADR 0024's budget now follows the same rule. One mutant per
    # resume path — leaving either outside the cap is the exact defect the
    # 2026-10-23 follow-up closed — plus one for the shared release.
    Mutant(
        "src/ai_assistant/agents/service.py",
        "            slots = self._acquire_run_slot()\n"
        "            try:\n"
        "                with self._model_lease():\n"
        "                    return self._approval_service.approve(request)",
        "            slots = None\n"
        "            try:\n"
        "                with self._model_lease():\n"
        "                    return self._approval_service.approve(request)",
        ("tests.unit.test_session_serialization",),
        "an approval resume bypasses the global run budget",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "            slots = self._acquire_run_slot()\n"
        "            try:\n"
        "                with self._model_lease():\n"
        "                    return self._ui_action_service.ui_action_result(request)",
        "            slots = None\n"
        "            try:\n"
        "                with self._model_lease():\n"
        "                    return self._ui_action_service.ui_action_result(request)",
        ("tests.unit.test_session_serialization",),
        "a desktop-ack resume bypasses the global run budget",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "        if slots is not None:\n            slots.release()",
        "        if False:\n            slots.release()",
        ("tests.unit.test_session_serialization",),
        "a run slot is never returned (the budget leaks until 503)",
    ),
    # ── At-rest encryption of local state (ADR 0027) ─────────────────────────
    # The encryption guards fail *closed* everywhere: a missing key, a wrong
    # key or a tampered row must abort the read instead of handing the caller
    # ciphertext or an empty store. Coverage cannot see that direction, so
    # each decision is inverted here.
    Mutant(
        "src/ai_assistant/adapters/persistence/storage_crypto.py",
        "    if explicit:\n"
        '        message = f"AI_STORAGE_KEY_FILE does not exist: {path}"\n'
        "        raise StorageKeyError(message)",
        "    if False:",
        ("tests.unit.test_storage_crypto",),
        "a missing explicit key file is replaced by a fresh key (existing state orphaned)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/storage_crypto.py",
        "            plaintext = self._aead.decrypt(\n"
        "                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _associated_data(context),\n"
        "            )\n"
        "        except (InvalidTag, ValueError) as error:",
        "            plaintext = self._aead.decrypt(\n"
        "                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _associated_data(context),\n"
        "            )\n"
        "        except ValueError as error:",
        ("tests.unit.test_storage_crypto",),
        "a tampered envelope escapes as a raw crypto error instead of a decryption failure",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/storage_crypto.py",
        "        if len(key) != KEY_BYTES:\n"
        '            message = f"at-rest key must decode to exactly {KEY_BYTES} bytes"\n'
        "            raise StorageKeyError(message)",
        "        if False:",
        ("tests.unit.test_storage_crypto",),
        "an at-rest key of the wrong length is accepted",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/storage_crypto.py",
        "    if _create_key_file(path, key):\n"
        "        return AesGcmFieldCipher(key)\n"
        "    # Another process won the O_EXCL race; its key is the one on disk.\n"
        "    return AesGcmFieldCipher(_read_key_file(path))",
        "    if _create_key_file(path, key):\n"
        "        return AesGcmFieldCipher(key)\n"
        "    return AesGcmFieldCipher(key)",
        ("tests.unit.test_storage_crypto",),
        "the process that lost the key-creation race keeps its own key (processes disagree)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/storage_crypto.py",
        "    _fsync_directory(path.parent)\n"
        "    return True",
        "    return True",
        ("tests.unit.test_storage_crypto",),
        "the key file's directory entry is never flushed (a crash orphans every row)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/pending_store.py",
        "                if self._cipher is None:\n"
        "                    if is_sealed(raw):",
        "                if self._cipher is None:\n"
        "                    if False:",
        ("tests.unit.test_pending_store_crypto",),
        "sealed pending actions are silently dropped instead of failing closed",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/pending_store.py",
        "                if self._cipher is not None:\n"
        '                    raw = self._cipher.seal(raw, context="pending.actions")',
        "                if False:\n"
        '                    raw = self._cipher.seal(raw, context="pending.actions")',
        ("tests.unit.test_pending_store_crypto",),
        "pending actions are written to disk as plaintext",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "    if cipher is None:\n"
        "        if is_sealed(value):",
        "    if cipher is None:\n"
        "        if False:",
        ("tests.unit.test_sqlite_store_crypto",),
        "sealed task state is served as ciphertext instead of failing closed",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "                if self._has_unsealed_content(cipher):",
        "                if False:",
        ("tests.unit.test_sqlite_store_crypto",),
        "a plaintext task store is accepted and migrated implicitly (the "
        "forge-the-schema-version attack)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/pending_store.py",
        "                elif not is_sealed(raw):",
        "                elif False:",
        ("tests.unit.test_pending_store_crypto",),
        "a plaintext pending-action file is accepted (the attacker's own "
        "pending approval is served to the user)",
    ),
    Mutant(
        "scripts/migrate_state_row_binding.py",
        "    try:\n"
        "        cipher.open(value, context=context)\n"
        "    except StorageCryptoError:\n"
        "        pass\n"
        "    else:\n"
        "        return value  # already row-bound: the script is idempotent",
        "    try:\n"
        "        cipher.open(value, context=context)\n"
        "    except StorageCryptoError:\n"
        "        pass",
        ("tests.unit.test_migrate_state_row_binding",),
        "an already-sealed value is re-sealed (the state becomes unreadable)",
    ),
    # ── At-rest key rotation (ADR 0030) ──────────────────────────────────────
    # The rotation is the only path that rewrites sealed state. It has to
    # fail closed in two directions: a value the old key cannot open must
    # abort the whole rotation, and a rotation that aborts must leave the
    # state exactly as it was — half of it re-encrypted would be readable
    # with neither key.
    Mutant(
        "src/ai_assistant/adapters/persistence/storage_crypto.py",
        "    except StorageDecryptionError:\n"
        "        new.open(value, context=context)\n"
        "        return value, False",
        "    except StorageDecryptionError:\n"
        "        return value, False",
        ("tests.unit.test_storage_rekey",),
        "the rotation rewrites a value the old key cannot open (wrong key accepted)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "        rekeyed = 0\n"
        "        with self._lock, self._conn:\n"
        "            for (task_id, message, result, error, metadata, row_identity, owner, classification,",
        "        rekeyed = 0\n"
        "        with self._lock:\n"
        "            for (task_id, message, result, error, metadata, row_identity, owner, classification,",
        ("tests.unit.test_storage_rekey",),
        "the rotation is not one transaction (an aborted rotation leaves half the state re-encrypted)",
    ),
    Mutant(
        "scripts/rekey_storage.py",
        "    report = _inspect(state_dir, old, new)\n"
        "    if report.failures:",
        "    report = _inspect(state_dir, old, new)\n"
        "    if False:",
        ("tests.unit.test_storage_rekey",),
        "the rotation starts without checking that the old key opens the state",
    ),
    # ── The command surface: a leading option is not "just a flag" ───────────
    # The allow-list used to accept *any* token that started with ``-`` as an
    # ordinary flag, so the verb was never checked: ``git -C <dir> clean
    # -fdx`` retargeted it, ``git -ccore.sshCommand=<cmd>`` injected config,
    # ``pytest -p <mod>`` imported code and ``ctest --build-and-test
    # --test-command <cmd>`` executed one. The pre-verb surface is an
    # allow-list now, and each of these guards has its own mutant.
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "            if token in flags or head in flags:",
        "            if True:",
        ("tests.unit.test_sandbox",),
        "an unknown leading flag is accepted (the verb can be hidden behind it)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "        if _looks_like_path(argument) and _path_escapes_workspace(argument, cwd):",
        "        if False:",
        ("tests.unit.test_sandbox",),
        "a git clone/init destination outside the workspace is accepted",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        'argument == "-c" or argument.startswith("-c")',
        "False",
        ("tests.unit.test_sandbox",),
        "the attached git config spelling (-ccore.sshCommand=…) is accepted",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "        if head in dangerous:",
        "        if False:",
        ("tests.unit.test_sandbox",),
        "an option that reads or writes a path outside the workspace is "
        "accepted (ruff --config, pytest --junitxml, git diff --no-index, …)",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        '        if token not in _ALLOWED_FLAGS["ctest"] and head not in _ALLOWED_FLAGS["ctest"]:',
        "        if False:",
        ("tests.unit.test_sandbox",),
        "ctest may build and run a command (--build-and-test --test-command)",
    ),
    # ── The write deny-list is a rule, not an enumeration ────────────────────
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        '    if _denied_tree_hit(relative):\n'
        '        return {"error": "Directory target is denied by sandbox policy (policy tree)."}',
        '    if False:\n'
        '        return {"error": "Directory target is denied by sandbox policy (policy tree)."}',
        ("tests.unit.test_sandbox",),
        "a directory is created inside the platform's own source",
    ),
    # ── The clearance gate: the principal's clearance covers the tool ────────
    Mutant(
        "src/ai_assistant/application/tools.py",
        "if not classification_at_least(principal.classification, spec.maximum_classification):",
        "if not classification_at_least(spec.maximum_classification, principal.classification):",
        ("tests.unit.test_tool_gateway_guards",),
        "the classification gate runs the other way round (the least trusted "
        "caller is let through and the most trusted is refused)",
    ),
    Mutant(
        "src/ai_assistant/domain/security.py",
        "    if scopes & _ELEVATED_SCOPES:",
        "    if False:",
        ("tests.unit.test_tool_gateway_guards",),
        "the clearance no longer follows the granted scopes",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/container.py",
        "}.get(label.casefold(), DataClassification.REGULATED)",
        "}.get(label.casefold(), DataClassification.PUBLIC)",
        ("tests.unit.test_gateway_bootstrap_guards",),
        "an unknown classification label lowers a tool's data ceiling",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        '_PATH_POSITIONAL_READERS = frozenset({"python", "pytest", "ruff"})',
        '_PATH_POSITIONAL_READERS = frozenset({"python", "pytest"})',
        ("tests.unit.test_sandbox",),
        "the binaries whose path positionals are confined lose that rule "
        "(ruff check <dir> prints the source lines of every file it finds)",
    ),
    # ── The isolation gate is on by default ──────────────────────────────────
    Mutant(
        "src/ai_assistant/settings.py",
        'values.get("AGENT_SANDBOX_REQUIRE_ISOLATION", "1") == "1"',
        'values.get("AGENT_SANDBOX_REQUIRE_ISOLATION", "0") == "1"',
        ("tests.unit.test_settings",),
        "the sandbox isolation gate is off by default again (the local runtime "
        "runs commands with the server's own privileges)",
    ),
    # ── The at-rest state is bound to its row ─────────────────────────────────
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        '    return f"task.v2:{task_id}:{field}"',
        '    return f"task.v2:{field}"',
        ("tests.unit.test_sqlite_store_crypto",),
        "the sealed value is not bound to its row (a value can be moved to "
        "another task and be served as that task's content)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "        if bundle.get(key) != actual:",
        "        if False:",
        ("tests.unit.test_sqlite_store_crypto",),
        "the row identity is not verified (the owner, the classification and "
        "the status can be forged)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "                if cipher is not None and has_rows:",
        "                if False:",
        ("tests.unit.test_sqlite_store_crypto",),
        "a state file that predates the row-bound sealing is opened instead "
        "of being refused",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/sqlite_store.py",
        "                    if value is not None and cipher.is_sealed(value):",
        "                    if False:",
        ("tests.unit.test_sqlite_store_crypto",),
        "the sealing action re-seals a state that is already sealed (the file "
        "claims the new version while its content stays in the old format)",
    ),
    # ── One rule per decision, and the signature is asked, not caught ────────
    Mutant(
        "src/ai_assistant/application/tools.py",
        "        success = tool_result_succeeded(payload)",
        "        success = not bool(payload.get(\"error\")) and payload.get(\"success\") is not False",
        ("tests.unit.test_tool_gateway_guards",),
        "the application re-spells the success rule and drops the exit code "
        "(a failed command is reported as a success)",
    ),
    Mutant(
        "src/ai_assistant/ports.py",
        '    return "since" in parameters',
        "    return True",
        ("tests.unit.test_task_lifecycle_guards",),
        "every store is assumed to accept the event cursor (a store that does "
        "not is called with a keyword it does not know)",
    ),
    Mutant(
        "src/ai_assistant/application/tasks.py",
        "        if store_supports_cursor(store_events):",
        "        if False:",
        ("tests.unit.test_task_lifecycle_guards",),
        "the event cursor is never asked for (the whole log is re-read on "
        "every tick)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/chat_routes.py",
        "        if not stat.S_ISREG(os.stat(path).st_mode):",
        "        if False:",
        ("tests.unit.test_http_chat_routes",),
        "a client-named path is opened even when it is not a regular file "
        "(a FIFO blocks the worker thread)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/chat_routes.py",
        "        if request.stream:",
        "        if False:",
        ("tests.unit.test_http_chat_routes",),
        "a requested chat stream is buffered into one JSON response",
    ),
    # ── The corpus is a second reader: one rule, one owner ───────────────────
    Mutant(
        "src/ai_assistant/rag/index.py",
        "            if not _stays_inside(root, path):",
        "            if False:",
        ("tests.unit.test_rag_corpus_boundary",),
        "the corpus follows a link out of the tree (a file outside the "
        "project is indexed and reaches the model)",
    ),
    Mutant(
        "src/ai_assistant/rag/index.py",
        "            if is_secret_name(filename):",
        "            if False:",
        ("tests.unit.test_rag_corpus_boundary",),
        "the corpus reads a file whose name is a secret name",
    ),
    Mutant(
        "src/ai_assistant/rag/index.py",
        "    return is_blocked_dir(name) or name in EXCLUDED_DIRS",
        "    return name in EXCLUDED_DIRS",
        ("tests.unit.test_rag_corpus_boundary",),
        "the corpus keeps its own directory list and drops the shared "
        "blocked-directory rule",
    ),
    Mutant(
        "src/ai_assistant/rag/index.py",
        "    for fp in _indexable_files(paths.project_root, SCANNABLE_EXTS, exclude=docs_dirs):",
        "    for fp in _indexable_files(paths.project_root, SCANNABLE_EXTS):",
        ("tests.unit.test_rag_corpus_boundary",),
        "the docs directories are walked twice (an image under Docs/ is "
        "indexed twice)",
    ),
    Mutant(
        "src/ai_assistant/rag/index.py",
        "        for path in _indexable_files(docs_dir, DOC_EXTS):",
        "        for path in ():",
        ("tests.unit.test_rag_corpus_boundary",),
        "the cache hash does not cover the documents (a changed document does "
        "not invalidate the cache)",
    ),
    # ── One catalogue, one policy: plugin tools and fail-closed bridges ──────
    Mutant(
        "src/ai_assistant/bootstrap/container.py",
        "    if name != raw_name:",
        "    if False:",
        ("tests.unit.test_gateway_bootstrap_guards",),
        "an alias is converted into an execution spec, so one desktop action "
        "gets a second (read-only) policy row",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/container.py",
        "        if entry.spec.plugin_id == BUILTIN_PLUGIN_ID:\n            continue",
        "        if False:\n            continue",
        ("tests.unit.test_plugin_tool_bridge",),
        "builtin tools are merged as plugin tools (the dynamic plugin row can "
        "grant a builtin the builtin specialists own)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/supervisor.py",
        "    if tool is not None and tool in plugin_tools:",
        "    if False:",
        ("tests.unit.test_plugin_tool_bridge",),
        "a plugin tool falls through to supervisor_fallback and is refused "
        "even when the composition root published it",
    ),
    Mutant(
        "src/ai_assistant/orchestration/supervisor.py",
        "    policy = ALLOWED_TOOLS if allowed_tools is None else allowed_tools",
        "    policy = ALLOWED_TOOLS",
        ("tests.unit.test_plugin_tool_bridge",),
        "the dynamic plugin row is ignored by authorise (the plugin tool is "
        "visible to the model but can never execute)",
    ),
    Mutant(
        "src/ai_assistant/plugins/registry.py",
        "        self._allowed_plugins = frozenset(allowed_plugins) | {BUILTIN_PLUGIN_ID}",
        "        self._allowed_plugins = frozenset(allowed_plugins)",
        ("tests.unit.test_plugins",),
        "the builtin catalogue follows the third-party allow-list (an operator "
        "config can disable the product's own tools)",
    ),
    Mutant(
        "src/ai_assistant/plugins/registry.py",
        "        if self._active_plugin_id is not None and spec.plugin_id != self._active_plugin_id:",
        "        if False:",
        ("tests.unit.test_plugins",),
        "a plugin borrows another enabled plugin's id during registration "
        "(provenance is not bound to the loaded plugin)",
    ),
    Mutant(
        "src/ai_assistant/plugins/registry.py",
        "            if spec.side_effect in {SideEffect.WRITE, SideEffect.EXECUTE} and not spec.requires_approval:",
        "            if False:",
        ("tests.unit.test_plugins",),
        "a mutating plugin tool is registered without approval",
    ),
    Mutant(
        "evals/run_model_in_loop_eval.py",
        "    if args.min_slice_accuracy is not None:\n",
        "    if False:\n",
        ("tests.unit.test_model_in_loop_eval",),
        "the release model gate ignores a broken language × planner-kind slice "
        "when the aggregate accuracy still passes",
    ),
    Mutant(
        "evals/run_rag_framework_eval.py",
        '        report["mean_ragas_context_precision"] >= min_precision',
        "        True",
        ("tests.unit.test_rag_framework_eval",),
        "the RAG framework release gate ignores a context-precision regression",
    ),
    Mutant(
        "evals/run_rag_framework_eval.py",
        '        and all(case["deepeval_passed"] for case in report["cases"])',
        "        and True",
        ("tests.unit.test_rag_framework_eval",),
        "a failing DeepEval case is hidden by passing aggregate means",
    ),
    Mutant(
        "src/ai_assistant/agents/session_locks.py",
        "                if entry.users == 0:\n                    self._entries.pop(session_id, None)",
        "                if False:\n                    self._entries.pop(session_id, None)",
        ("tests.unit.test_session_serialization",),
        "the session lock entry is never released (the process retains one "
        "lock per session id it has ever seen)",
    ),
    Mutant(
        "src/ai_assistant/agents/models.py",
        "    session_id:          str   = Field(default=\"\", max_length=128)",
        "    session_id:          str   = Field(default=\"\")",
        ("tests.unit.test_session_serialization",),
        "the execute session id is unbounded (the lock pool and checkpointer "
        "thread id retain an arbitrarily large key)",
    ),
    Mutant(
        "src/ai_assistant/adapters/mcp.py",
        "    if getattr(spec, \"requires_approval\", False):\n        return False",
        "    if False:\n        return False",
        ("tests.unit.test_mcp_adapter",),
        "MCP advertises an approval-gated tool it has no HITL flow to approve",
    ),
    Mutant(
        "src/ai_assistant/adapters/mcp.py",
        "    if canonical_tool_name(str(spec.name)) != spec.name:\n        return False",
        "    if False:\n        return False",
        ("tests.unit.test_mcp_adapter",),
        "an alias is advertised as a second MCP tool with no execution policy",
    ),
    # ── LLM egress and artifact integrity ───────────────────────────────────
    Mutant(
        "src/ai_assistant/llm/inference.py",
        '    return policy == "cloud_allowed" and data_classification.casefold() in {"public", "non_sensitive"}',
        '    return policy == "cloud_allowed" or data_classification.casefold() in {"public", "non_sensitive"}',
        ("tests.unit.test_inference",),
        "the privacy gate ORs the policy with the classification, so "
        "local_only still routes public data to a remote model",
    ),
    Mutant(
        "src/ai_assistant/llm/inference.py",
        '    if not cloud_allowed("non_sensitive"):',
        "    if False:",
        ("tests.unit.test_inference",),
        "remote completion skips the cloud-policy gate (bytes leave before "
        "the operator opted in)",
    ),
    Mutant(
        "src/ai_assistant/llm/inference.py",
        "        if len(body) > _MAX_RESPONSE_BYTES:",
        "        if False:",
        ("tests.unit.test_inference",),
        "the remote response size bound is removed (a degraded peer can make "
        "the client buffer without limit)",
    ),
    Mutant(
        "src/ai_assistant/llm/model_loader.py",
        "    if not expected_sha256:\n        return True",
        "    if False:\n        return True",
        ("tests.unit.test_llm_backend",),
        "an unpinned artifact is compared against the empty string and can "
        "never load (or, with the inverse policy, is treated as pinned)",
    ),
    Mutant(
        "src/ai_assistant/llm/model_loader.py",
        "        return _sha256_of(path).casefold() == expected_sha256.casefold()",
        "        return _sha256_of(path).casefold() == expected_sha256",
        ("tests.unit.test_llm_backend",),
        "the pinned SHA-256 comparison becomes case-sensitive (an uppercase "
        "pin rejects the same artifact)",
    ),
    Mutant(
        "src/ai_assistant/llm/model_loader.py",
        "        if _matches_pin(path, sha256):\n            return path",
        "        if False:\n            return path",
        ("tests.unit.test_llm_backend",),
        "an already-downloaded pinned artifact is discarded and downloaded "
        "again on every load",
    ),
    Mutant(
        "src/ai_assistant/llm/model_loader.py",
        "    if not _matches_pin(path, sha256):",
        "    if False:",
        ("tests.unit.test_llm_backend",),
        "a post-download hash mismatch loads unverified weights instead of "
        "raising ModelIntegrityError",
    ),
    Mutant(
        "src/ai_assistant/llm/model_loader.py",
        "    if model.mmproj_repo_id == model.repo_id:",
        "    if model.mmproj_repo_id != model.repo_id:",
        ("tests.unit.test_llm_backend",),
        "the vision projector inherits the model revision across repos (the "
        "wrong commit is downloaded) and stays unpinned in its own repo",
    ),
    # ── The internal framework boundary ─────────────────────────────────────
    Mutant(
        "src/ai_assistant/internal/langgraph_engine.py",
        "        return CompiledGraph(self._builder.compile(checkpointer=checkpointer))",
        "        return CompiledGraph(self._builder.compile())",
        ("tests.unit.test_langgraph_engine_wrapper",),
        "compile drops the checkpointer (resumable runs become stateless)",
    ),
    Mutant(
        "src/ai_assistant/internal/langgraph_engine.py",
        "        return cast(StateT, self._graph.invoke(state, config=config))",
        "        return cast(StateT, self._graph.invoke(state, config={}))",
        ("tests.unit.test_langgraph_engine_wrapper",),
        "invoke drops the caller's config (thread id and checkpoint settings "
        "never reach the compiled graph)",
    ),
    Mutant(
        "src/ai_assistant/internal/langgraph_engine.py",
        "        return self._graph.get_state(config)",
        "        return None",
        ("tests.unit.test_langgraph_engine_wrapper",),
        "get_state stops delegating (resume reads no checkpoint)",
    ),
    # ── One model lease per entry point that runs the graph (ADR 0040) ───────
    Mutant(
        "src/ai_assistant/agents/service.py",
        "                with self._model_lease():\n"
        "                    return self._execute_locked(",
        "                if True:\n"
        "                    return self._execute_locked(",
        ("tests.unit.test_agent_service_guards",),
        "the fresh run executes without a lease (a reload frees the model "
        "under a running graph)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "        with self._hold_session(request.session_id):\n"
        "            slots = self._acquire_run_slot()\n"
        "            try:\n"
        "                with self._model_lease():\n"
        "                    return self._approval_service.approve(request)\n"
        "            finally:\n"
        "                self._release_run_slot(slots)",
        "        with self._hold_session(request.session_id):\n"
        "            slots = self._acquire_run_slot()\n"
        "            try:\n"
        "                if True:\n"
        "                    return self._approval_service.approve(request)\n"
        "            finally:\n"
        "                self._release_run_slot(slots)",
        ("tests.unit.test_agent_service_guards",),
        "the approval resume executes without a lease (only fresh runs block "
        "the reload)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "        with self._hold_session(str(parked.get(\"session_id\", \"\"))):\n"
        "            slots = self._acquire_run_slot()\n"
        "            try:\n"
        "                with self._model_lease():\n"
        "                    return self._ui_action_service.ui_action_result(request)\n"
        "            finally:\n"
        "                self._release_run_slot(slots)",
        "        with self._hold_session(str(parked.get(\"session_id\", \"\"))):\n"
        "            slots = self._acquire_run_slot()\n"
        "            try:\n"
        "                if True:\n"
        "                    return self._ui_action_service.ui_action_result(request)\n"
        "            finally:\n"
        "                self._release_run_slot(slots)",
        ("tests.unit.test_agent_service_guards",),
        "the desktop-ack resume executes without a lease (only fresh runs "
        "block the reload)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/chat_routes.py",
        "        with llm_module.model_lease() as backend:\n"
        "            if backend is None:\n"
        "                # The fast check above saw a model, but a reload may have won",
        "        if True:\n"
        "            backend = llm_module.llm\n"
        "            if backend is None:\n"
        "                # The fast check above saw a model, but a reload may have won",
        ("tests.unit.test_http_chat_routes",),
        "chat inference runs outside the model lease (a reload frees the "
        "backend under the stream)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/chat_routes.py",
        "    try:\n"
        "        with llm_module.model_lease() as backend:\n"
        "            if backend is None:",
        "    try:\n"
        "        if True:\n"
        "            backend = llm_module.llm\n"
        "            if backend is None:",
        ("tests.unit.test_http_chat_routes",),
        "streaming chat releases its model lease before consuming chunks",
    ),
    # ── One owner for the model, and a release that really releases (ADR 0041) ─
    Mutant(
        "src/ai_assistant/bootstrap/modules.py",
        "        had_backend = self.llm is not None\n        self.llm = None",
        "        had_backend = self.llm is not None\n        pass",
        ("tests.unit.test_bootstrap_coverage_gaps",),
        "release keeps the backend reference (VRAM stays resident while the "
        "route returns ok, the exact defect the single owner removed)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/modules.py",
        "            # Free the previous weights before the new download/constructor:\n"
        "            # two models must never be resident at once on a GPU.\n"
        "            with self.llm_lock:\n"
        "                had_backend = self._clear_backend_locked()\n"
        "            if had_backend:\n"
        "                release_ml_memory()",
        "            # Free the previous weights before the new download/constructor:\n"
        "            # two models must never be resident at once on a GPU.\n"
        "            with self.llm_lock:\n"
        "                had_backend = self._clear_backend_locked()\n"
        "            if False:\n"
        "                release_ml_memory()",
        ("tests.unit.test_bootstrap_coverage_gaps",),
        "a reload drops the old reference without asking the runtime to free "
        "it (both models stay resident during the swap)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/health_routes.py",
        "        if llm_module.llm is None:\n            return {\"data\": []}",
        "        if False:\n            return {\"data\": []}",
        ("tests.unit.test_http_health_routes",),
        "a released model is still advertised by /v1/models (clients route "
        "requests to weights that no longer exist)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/health_routes.py",
        "            \"model\": llm_module.active_model_desc if loaded else \"\",",
        "            \"model\": llm_module.active_model_desc,",
        ("tests.unit.test_http_health_routes",),
        "/health advertises the previous model after release (one endpoint "
        "says unloaded while the other names the model)",
    ),
    # ── Admission happens before the worker thread exists (ADR 0041) ─────────
    Mutant(
        "src/ai_assistant/application/tasks.py",
        "        if not self._try_acquire_slot():\n"
        "            return self._reject_at_capacity(task)",
        "        if False:\n"
        "            return self._reject_at_capacity(task)",
        ("tests.unit.test_task_lifecycle_guards",),
        "a submit at capacity creates a worker anyway (the burst pays for the "
        "thread before the executor answers 503)",
    ),
    Mutant(
        "src/ai_assistant/application/tasks.py",
        "        if not self._try_acquire_slot():\n"
        "            raise TaskCapacityError(",
        "        if False:\n"
        "            raise TaskCapacityError(",
        ("tests.unit.test_task_lifecycle_guards",),
        "a resume at capacity starts a second worker (the admission bound "
        "covers submits but not resumes)",
    ),
    Mutant(
        "src/ai_assistant/application/tasks.py",
        "                self._release_slot()\n"
        "                with self._worker_lock:",
        "                if False:\n"
        "                    self._release_slot()\n"
        "                with self._worker_lock:",
        ("tests.unit.test_task_lifecycle_guards",),
        "a finished worker never returns its slot (capacity is permanently "
        "lost until restart)",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_router.py",
        "    except TaskCapacityError as error:\n"
        "        raise HTTPException(\n"
        "            status_code=429, detail=str(error), headers={\"Retry-After\": \"1\"},\n"
        "        ) from error",
        "    except TaskCapacityError as error:\n"
        "        raise HTTPException(\n"
        "            status_code=409, detail=str(error), headers={\"Retry-After\": \"1\"},\n"
        "        ) from error",
        ("tests.unit.test_a2a_http_router",),
        "capacity exhaustion is reported as a permanent conflict instead of "
        "retryable backpressure",
    ),
    Mutant(
        "src/ai_assistant/adapters/a2a_router.py",
        "            return {\"jsonrpc\": \"2.0\", \"id\": request_id,\n"
        "                    \"error\": {\"code\": -32000, \"message\": str(error)}}",
        "            return {\"jsonrpc\": \"2.0\", \"id\": request_id,\n"
        "                    \"error\": {\"code\": -32602, \"message\": str(error)}}",
        ("tests.unit.test_a2a_http_router",),
        "a capacity refusal is reported as invalid params (the client retries "
        "the request instead of backing off)",
    ),
    # ── The tenth review: owner-enforced swaps, fail-closed defaults, public
    #    RAG surface and a graceful stop (ADR 0042) ────────────────────────────
    Mutant(
        "src/ai_assistant/bootstrap/modules.py",
        "        with self._model_condition:\n"
        "            if not self._reloading:",
        "        with self._model_condition:\n"
        "            if False:",
        ("tests.unit.test_bootstrap_coverage_gaps",),
        "load/release is callable outside model_reload(): a route can free "
        "the backend while a run holds a lease",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/server.py",
        "    with runtime_modules.llm.model_reload():\n"
        "        runtime_modules.llm.load_model()",
        "    runtime_modules.llm.load_model()",
        ("tests.integration.test_composition_root_bootstrap",),
        "startup loads the model outside the drain window (the owner refuses "
        "it; bootstrap regresses to a direct primitive call)",
    ),
    Mutant(
        "src/ai_assistant/domain/security.py",
        "    if capability_scopes is None:\n"
        "        return READ_ONLY_SCOPES",
        "    if capability_scopes is None:\n"
        "        return frozenset({\"project.read\", \"project.write\", \"project.execute\"})",
        ("tests.unit.test_final_edges_a",),
        "a host that injects no scope map grants the trusted write/execute "
        "scopes to every embedded caller",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/container.py",
        "    if policy_value is None:\n"
        "        # A missing label is not \"read-only\": a mutating tool whose policy was\n"
        "        # never authored would otherwise inherit the weakest scope and could\n"
        "        # be driven by a read-only principal. Unknown labels already fail in\n"
        "        # ``_side_effect``; missing ones must fail here for the same reason.\n"
        "        msg = f\"Tool {name!r} has no execution policy\"\n"
        "        raise ValueError(msg)",
        "    if policy_value is None:\n"
        "        policy_value = \"read_only\"",
        ("tests.unit.test_gateway_bootstrap_guards",),
        "a tool with no authored policy silently becomes read-only (a "
        "mutating tool inherits the weakest scope)",
    ),
    Mutant(
        "src/ai_assistant/tools/factory.py",
        "    if not config_file.exists():\n"
        "        # A server that starts without a catalogue advertises itself as ready\n"
        "        # but can execute nothing: the agent has no tools, and every request\n"
        "        # fails far from the cause. A missing required catalogue is a\n"
        "        # configuration error, not an empty-but-valid registry.\n"
        "        msg = f\"Tool catalogue not found: {config_file}\"\n"
        "        raise ConfigurationError(msg)",
        "    if not config_file.exists():\n"
        "        return registry",
        ("tests.unit.test_final_edges_b",),
        "a missing tool catalogue degrades into an empty registry (the server "
        "reports ready while it can execute nothing)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/modules.py",
        "        self.shutdown_requested.set()",
        "        pass",
        ("tests.unit.test_bootstrap_coverage_gaps",),
        "/admin/shutdown never signals the serving process (the stop is "
        "requested but the event stays clear)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/cli.py",
        "    shutdown_requested.wait()\n"
        "    server.should_exit = True",
        "    shutdown_requested.wait()\n"
        "    server.should_exit = False",
        ("tests.integration.test_main_entry_points",),
        "the shutdown watcher never asks uvicorn to stop (graceful exit "
        "silently becomes a no-op)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/admin_routes.py",
        "            request_shutdown()",
        "            pass",
        ("tests.unit.test_http_admin_routes",),
        "/admin/shutdown answers ok without asking the process to stop",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/modules.py",
        "        return self._reranker is not None",
        "        return True",
        ("tests.unit.test_bootstrap_coverage_gaps",),
        "the public reranker fact always reports active (health lies about the "
        "retrieval pipeline)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/health_routes.py",
        "            \"reranker\": rag_module.reranker_active,",
        "            \"reranker\": False,",
        ("tests.unit.test_http_health_routes",),
        "/health never reports an active reranker",
    ),
    # ── Memory: short/long-term policy (ADR 0043) ─────────────────────────────
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "        if query_tokens and overlap == 0.0:\n"
        "            continue",
        "        if False:\n"
        "            continue",
        ("tests.unit.test_memory_domain",),
        "a record with no keyword overlap is recalled anyway (unrelated "
        "\"memories\" are injected into the prompt as noise)",
    ),
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "    return isinstance(value, str) and value in MEMORY_KINDS",
        "    return isinstance(value, str)",
        ("tests.unit.test_memory_domain",),
        "an unknown rehydrated kind is accepted by the runtime gate (a kind "
        "with no policy reaches the recall path)",
    ),
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "    while total > max_tokens and len(trimmed) > 1:",
        "    while total > max_tokens:",
        ("tests.unit.test_memory_domain",),
        "the drop-oldest window may trim the history down to nothing (the "
        "next model call loses every turn)",
    ),
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "    if total > CONTEXT_TOTAL_LIMIT:\n"
        "        for index in range(1, len(result)):",
        "    if False:\n"
        "        for index in range(1, len(result)):",
        ("tests.unit.test_summarize_guards", "tests.unit.test_memory_domain"),
        "an over-budget compacted history is handed to the model unchanged "
        "(the context window overruns)",
    ),
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "    return h >= n and any(haystack[i:i + n] == list(needle) for i in range(h - n + 1))",
        "    return False",
        ("tests.unit.test_memory_domain",),
        "the window rule never matches (a client that ships no history gets "
        "no context back — the buffer exists but never answers)",
    ),
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "    return [dict(m) for m in buffered] + [dict(m) for m in incoming[overlap:]]",
        "    return [dict(m) for m in incoming[overlap:]]",
        ("tests.unit.test_memory_domain",),
        "the merge drops the remembered context and keeps only what the client "
        "shipped (the short-term buffer never contributes)",
    ),
    Mutant(
        "src/ai_assistant/agents/short_term_memory.py",
        "        if not turns:",
        "        if False:",
        ("tests.unit.test_short_term_memory",),
        "recording an empty message list erases the window the buffer exists "
        "to keep",
    ),
    Mutant(
        "src/ai_assistant/agents/short_term_memory.py",
        "        while len(owned) > self._max_sessions:",
        "        while False:",
        ("tests.unit.test_short_term_memory",),
        "the session map never evicts (a client inventing session ids leaks "
        "the buffer without bound)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "        if short_term is not None:\n"
        "            history_messages = merge_history(short_term.recent(session_id), history_messages)",
        "        if False:\n"
        "            history_messages = merge_history(short_term.recent(session_id), history_messages)",
        ("tests.unit.test_short_term_memory",),
        "the shipped history is never topped up from the buffer (short-term "
        "memory is written but never read)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "        if short_term is not None:\n"
        "            turns = list(history_messages)",
        "        if False:\n"
        "            turns = list(history_messages)",
        ("tests.unit.test_short_term_memory",),
        "the run's turns are never recorded back (the buffer only ever holds "
        "what a test put there)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "            if answer:",
        "            if False:",
        ("tests.unit.test_short_term_memory",),
        "the assistant turn is recorded even when the run never concluded "
        "(a parked run invents an answer in the remembered context)",
    ),
    # ── Long-term memory loop (ADR 0043 §3-§5) ────────────────────────────────
    Mutant(
        "src/ai_assistant/application/memory.py",
        '            logger.warning("Long-term memory recall failed: %s", error)\n'
        "            return []",
        '            logger.warning("Long-term memory recall failed: %s", error)\n'
        "            raise",
        ("tests.unit.test_memory_service",),
        "recall re-raises a store failure (a memory problem can fail a run)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        '        trusted = [record for record in candidates if is_memory_kind(record.get("kind"))]',
        "        trusted = list(candidates)",
        ("tests.unit.test_memory_service",),
        "an unknown-kind candidate is not re-checked at the trust crossing "
        "(a kind with no policy reaches the prompt)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "            if not isinstance(safe_text, str) or has_sensitive_content(safe_text):",
        "            if False:",
        ("tests.unit.test_memory_service",),
        "the redaction gate never drops a still-sensitive record (a token "
        "shape that survived redaction is persisted and recalled later)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        '                logger.warning("Long-term memory write failed for %s: %s",\n'
        '                               record["id"], error)\n'
        "                continue",
        '                logger.warning("Long-term memory write failed for %s: %s",\n'
        '                               record["id"], error)\n'
        "                raise",
        ("tests.unit.test_memory_service",),
        "a failing store write is not contained (consolidation fails the "
        "run that already succeeded)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "        return self._store is not None and self._top_k > 0",
        "        return self._store is not None",
        ("tests.unit.test_memory_service",),
        "a zero top_k still reports memory as enabled (the off switch is ignored)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        "        if cipher is None:",
        "        if False:",
        ("tests.unit.test_memory_store",),
        "a store without a key is built anyway (user facts at rest in plaintext)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        "        if not is_memory_kind(kind):",
        "        if False:",
        ("tests.unit.test_memory_store",),
        "an unknown memory kind is accepted on the way in (the closed "
        "vocabulary is open from the write side)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        '        if not is_memory_kind(sealed_kind) or sealed_kind != kind or data.get("owner") != owner:',
        "        if False:",
        ("tests.unit.test_memory_store",),
        "a sealed identity that disagrees with the plain-text columns is "
        "served (the columns stop being a filter hint)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        "        if created is None or last_used is None or salience_value is None:",
        "        if False:",
        ("tests.unit.test_memory_store",),
        "a non-numeric column is served instead of skipping its row (the "
        "tampered row reaches the ranker, and the caller loses the healthy "
        "rows the old raise used to take with it)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        "            if max_bytes > 0:",
        "            if True:",
        ("tests.unit.test_memory_store",),
        "prune without a byte cap shrinks the store to zero bytes",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        "        cutoff = time.time() - max(1, int(retention_days)) * 86400.0",
        "        cutoff = time.time() - int(retention_days) * 86400.0",
        ("tests.unit.test_memory_store",),
        "a non-positive retention wipes the store (every row is already past "
        "the cutoff)",
    ),
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "    return facts[:MAX_SEMANTIC_PER_RUN]",
        "    return facts",
        ("tests.unit.test_memory_domain",),
        "the semantic per-run cap is removed (one run can flood the store "
        "with facts)",
    ),
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "    if plan_items:",
        "    if True:",
        ("tests.unit.test_memory_domain",),
        "a run without a plan gets a procedural record anyway (an empty "
        "recipe keyed by the task signature overwrites the real one)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "        if memory is not None and memory.enabled:\n"
        "            recalled = memory.recall(task_with_attachments, owner=owner)",
        "        if False:\n"
        "            recalled = memory.recall(task_with_attachments, owner=owner)",
        ("tests.unit.test_memory_service",),
        "long-term recall is never applied before a run (memory is written "
        "but never read)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "            if memory_block:",
        "            if True:",
        ("tests.unit.test_memory_service",),
        "an empty recall still appends a memory block and announces a "
        "memory_recall step (the model is told there is memory when there "
        "is none)",
    ),
    # ── Consolidation on every completion path (ADR 0043 §10) ───────────────
    Mutant(
        "src/ai_assistant/agents/response.py",
        '            "owner": owner,',
        '            "owner": "default",',
        ("tests.unit.test_runner",),
        "a parked approval/UI action forgets the run's tenant (the resume "
        "consolidates into the default namespace)",
    ),
    Mutant(
        "src/ai_assistant/agents/context.py",
        '        if result.get("status") != "completed":\n'
        "            return",
        "        if False:\n"
        "            return",
        ("tests.unit.test_agent_run_context", "tests.unit.test_memory_service"),
        "a parked or failed run is consolidated into long-term memory",
    ),
    Mutant(
        "src/ai_assistant/agents/context.py",
        "        memory.consolidate_run(task, session_id, result.get(\"steps\") or [], owner=owner)",
        "        memory.consolidate_run(task, session_id, result.get(\"steps\") or [])",
        ("tests.unit.test_agent_run_context", "tests.unit.test_memory_service"),
        "consolidation ignores the resume's owner (a tenant's completed "
        "resume is written into the default namespace)",
    ),
    Mutant(
        "src/ai_assistant/agents/ui_action_service.py",
        '                "owner": owner,',
        '                "owner": DEFAULT_MEMORY_OWNER,',
        ("tests.unit.test_agent_resume_services",),
        "a chained desktop action drops the run's tenant (the next ack "
        "resumes under the default namespace)",
    ),
    Mutant(
        "src/ai_assistant/agents/approval_service.py",
        "        self._ctx.consolidate_completed(\n"
        '            result, task=action["task"], session_id=action["session_id"], owner=owner,\n'
        "        )\n"
        "        return result",
        "        return result",
        ("tests.unit.test_agent_resume_services",),
        "a no-graph approval completes without remembering the run",
    ),
    Mutant(
        "src/ai_assistant/agents/ui_action_service.py",
        "        self._ctx.consolidate_completed(\n"
        '            result, task=action["task"], session_id=action["session_id"], owner=owner,\n'
        "        )\n"
        "        return result",
        "        return result",
        ("tests.unit.test_agent_resume_services",),
        "a desktop workflow finalised without a graph resume is never "
        "remembered",
    ),
    # ── Procedural memory seed (ADR 0043 §7) ──────────────────────────────────
    Mutant(
        "src/ai_assistant/application/memory.py",
        "        if self._store is None or self._top_k <= 0:\n"
        "            return None\n"
        "        target = self._resolve_owner(owner)",
        "        if self._store is None:\n"
        "            return None\n"
        "        target = self._resolve_owner(owner)",
        ("tests.unit.test_memory_service",),
        "the top_k off switch does not disable the procedural lookup "
        "(AGENT_MEMORY_LTM_TOP_K=0 turns off only half of memory)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        '            logger.warning("Procedural memory lookup failed: %s", error)\n'
        "            return None",
        '            logger.warning("Procedural memory lookup failed: %s", error)\n'
        "            raise",
        ("tests.unit.test_memory_service",),
        "a store failure propagates out of the procedural lookup (a memory "
        "problem fails the run before it starts)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "            if record.get(\"kind\") != \"procedural\":\n"
        "                continue",
        "            if False:\n"
        "                continue",
        ("tests.unit.test_memory_service",),
        "any kind's record is used as a remembered plan (a semantic fact "
        "whose metadata matches becomes a recipe)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "            if not isinstance(metadata, Mapping) or metadata.get(\"task_signature\") != signature:",
        "            if not isinstance(metadata, Mapping):",
        ("tests.unit.test_memory_service",),
        "a remembered plan is reused for a different task (the signature "
        "comparison is gone, so any procedural row answers any query)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "            if not isinstance(text, str):\n"
        "                continue",
        "            if False:\n"
        "                continue",
        ("tests.unit.test_memory_service",),
        "non-string plan text is split anyway (the lookup crashes on data "
        "the sealed payload should never have produced)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "            if steps:\n"
        "                return steps",
        "            if True:\n"
        "                return steps",
        ("tests.unit.test_memory_service",),
        "text that splits into nothing returns an empty plan instead of "
        "None (the caller cannot tell 'no recipe' from 'empty recipe')",
    ),
    # ── Memory ownership (ADR 0043) ──────────────────────────────────────────
    Mutant(
        "src/ai_assistant/domain/memory.py",
        "    if owner == DEFAULT_MEMORY_OWNER:\n"
        '        return ""',
        "    if True:\n"
        '        return ""',
        ("tests.unit.test_memory_domain",),
        "every owner keeps the historical un-namespaced id (two tenants "
        "running the same task collide on the primary key and replace each "
        "other's procedural row)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "        resolved = owner.strip()\n"
        "        if not resolved:",
        "        resolved = owner.strip()\n"
        "        if False:",
        ("tests.unit.test_memory_service",),
        "an empty owner is accepted (every read misses and every write lands "
        "in a namespace nothing reads)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "        return self._owner if owner is None else self._validated_owner(owner)",
        "        return self._owner",
        ("tests.unit.test_memory_service",),
        "the per-call owner is ignored (the A2A tenant always falls back to "
        "the composition's default namespace)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "        for record in build_run_records(task, session_id, list(steps), target):",
        "        for record in build_run_records(task, session_id, list(steps), self._owner):",
        ("tests.unit.test_memory_service",),
        "consolidation writes under the configured owner instead of the "
        "run's tenant",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "        if stored and self._retention_days > 0:",
        "        if False:",
        ("tests.unit.test_memory_service",),
        "a successful consolidation never runs retention (the store grows "
        "between process restarts)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        '            if existing is not None and existing[0] != record["owner"]:',
        "            if False:",
        ("tests.unit.test_memory_store",),
        "a cross-owner id collision replaces the row (another tenant's "
        "memory is erased even though the id namespace was supposed to keep "
        "them apart)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "            recalled = memory.recall(task_with_attachments, owner=owner)",
        "            recalled = memory.recall(task_with_attachments)",
        ("tests.unit.test_memory_service",),
        "recall ignores the run's owner (an A2A run reads the default "
        "tenant's memory)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "            prior_plan = memory.procedural_plan(task_with_attachments, owner=owner)",
        "            prior_plan = memory.procedural_plan(task_with_attachments)",
        ("tests.unit.test_memory_service",),
        "the procedural lookup ignores the run's owner (a tenant reuses the "
        "default namespace's plan)",
    ),
    Mutant(
        "src/ai_assistant/adapters/orchestration/langgraph.py",
        "            return self._map(self._execute(self._execute_request(task), owner=task.owner))",
        "            return self._map(self._execute(self._execute_request(task)))",
        ("tests.unit.test_langgraph_adapter",),
        "the task's tenant is not forwarded to the agent engine (every A2A "
        "run falls back to the engine's default owner)",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/agent_routes.py",
        "                    request, owner=DEFAULT_MEMORY_OWNER,",
        '                    request, owner="tenant-x",',
        ("tests.unit.test_http_agent_routes",),
        "the loopback execute route scopes a local run to a made-up tenant "
        "instead of the desktop default",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/agent_routes.py",
        "            request, owner=DEFAULT_MEMORY_OWNER, client_host=client_host,",
        '            request, owner="tenant-x", client_host=client_host,',
        ("tests.unit.test_http_agent_routes",),
        "the loopback SSE route scopes a local stream to a made-up tenant "
        "instead of the desktop default",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/agent_routes.py",
        "            if self._closing:\n                return False",
        "            if False:\n                return False",
        ("tests.unit.test_http_agent_routes",),
        "an SSE worker can start after shutdown admission closed (the new run "
        "may race stores that the lifespan is about to release)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/specialists/chatbot.py",
        '    return decision if decision in {"WEB_SEARCH", "DIRECT"} else None',
        "    return decision",
        ("tests.unit.test_web_search",),
        "an unrecognised freshness answer becomes a valid routing decision "
        "instead of failing closed without external egress",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/plan.py",
        "    remembered = state.get(\"prior_plan\")\n"
        "    if remembered:",
        "    remembered = state.get(\"prior_plan\")\n"
        "    if False:",
        ("tests.unit.test_plan_node_guards",),
        "a remembered plan is never reused (the planner and its critic run "
        "again for a task that already has a passing recipe)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/plan.py",
        "            \"plan_verified\": True,\n"
        "            \"steps\": steps,",
        "            \"plan_verified\": False,\n"
        "            \"steps\": steps,",
        ("tests.unit.test_plan_node_guards",),
        "a remembered plan is not marked verified (the critic re-runs on "
        "every reuse, which is the cost the seed exists to avoid)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/plan.py",
        "            \"plan_attempts\": state.get(\"plan_attempts\", 0) + 1,\n"
        "            \"plan_feedback\": \"\",",
        "            \"plan_attempts\": state.get(\"plan_attempts\", 0),\n"
        "            \"plan_feedback\": \"\",",
        ("tests.unit.test_plan_node_guards",),
        "a memory seed does not count as a plan attempt (the in-run counter "
        "and the resume recount of plan steps disagree)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "            prior_plan = memory.procedural_plan(task_with_attachments, owner=owner)",
        "            prior_plan = None",
        ("tests.unit.test_memory_service",),
        "the facade never looks up a remembered plan (procedural memory is "
        "written but never read)",
    ),
    Mutant(
        "src/ai_assistant/agents/context.py",
        "            prior_plan=prior_plan,\n"
        "            initial_iteration=initial_iteration,",
        "            prior_plan=None,\n"
        "            initial_iteration=initial_iteration,",
        ("tests.unit.test_agent_run_context",),
        "the context drops prior_plan on its way to the runner",
    ),
    Mutant(
        "src/ai_assistant/agents/runner.py",
        "            enforce_plan_completion=True,\n"
        "            prior_plan=prior_plan,",
        "            enforce_plan_completion=True,\n"
        "            prior_plan=None,",
        ("tests.unit.test_runner",),
        "the runner does not forward prior_plan to graph.run",
    ),
    Mutant(
        "src/ai_assistant/orchestration/graph.py",
        "            \"prior_plan\": prior_plan,",
        "            \"prior_plan\": None,",
        ("tests.unit.test_graph_run_guards",),
        "graph.run never puts the remembered plan into the state (the seed "
        "has nothing to read)",
    ),
    # ── Parallel tool batches: eligibility, fan-out, merge (ADR 0044) ────────
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        '    if "rag_search" in ctx.requires_approval:\n'
        "        return None",
        "    if False:\n"
        "        return None",
        ("tests.unit.test_parallel_batch",),
        "an approval-gated group fans out (a branch spends the grant the user "
        "gave for one visible invocation, and the steps skip the order)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        "    if ctx.max_parallel <= 1:\n"
        "        return None",
        "    if False:\n"
        "        return None",
        ("tests.unit.test_parallel_batch",),
        "a single-threaded deployment still batches (the width setting is "
        "ignored)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        "                    if ctx.cancel_checker():\n"
        "                        cancelled = True\n"
        '                        results[index] = {"error": "Tool batch branch cancelled."}\n'
        "                        continue",
        "                    if False:\n"
        "                        cancelled = True\n"
        '                        results[index] = {"error": "Tool batch branch cancelled."}\n'
        "                        continue",
        ("tests.unit.test_parallel_batch",),
        "a cancelled run keeps starting the remaining branches (the cancel "
        "gate is checked once, before the fan-out)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        "                for index, future in futures.items():\n"
        "                    results[index] = future.result()",
        "                pass",
        ("tests.unit.test_parallel_batch",),
        "the batch returns before its branches are joined (the state update "
        "races every worker it started)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        '    return state.get("error_count", 0) + failures if failures else 0',
        '    return state.get("error_count", 0) + failures',
        ("tests.unit.test_parallel_batch",),
        "a clean group never resets the error streak (an old failure still "
        "trips the circuit breaker)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        "                                batch_evidence=siblings)",
        "                                batch_evidence=None)",
        ("tests.unit.test_parallel_batch",),
        "the siblings' evidence is dropped from the synthesis turn (the model "
        "must search again for steps it already fetched)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        "calls=len(batch.calls),",
        "calls=1,",
        ("tests.unit.test_parallel_batch",),
        "a whole group is charged as one tool call (the per-call budget the "
        "circuit breaker and the UI rely on no longer matches the log)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        '    steps[batch.head_index] = {**batch.head_step, "batch_id": batch.batch_id,',
        '    steps[0] = {**batch.head_step, "batch_id": batch.batch_id,',
        ("tests.unit.test_parallel_batch",),
        "the batch label lands on the first step of the log instead of the "
        "head (an earlier, already-answered call is rewritten)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        '        if kinds[index] != "rag_search":\n'
        "            return None",
        "        if False:\n"
        "            return None",
        ("tests.unit.test_parallel_batch",),
        "a direct/application member is batched (the model owes that step its "
        "answer or its approval, and a worker thread cannot provide either)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        '    if tool_name != "rag_search":\n'
        "        return None\n"
        '    if "rag_search" in ctx.requires_approval:',
        "    if False:\n"
        "        return None\n"
        '    if "rag_search" in ctx.requires_approval:',
        ("tests.unit.test_parallel_batch",),
        "a non-RAG head starts a RAG group (the pending call and the group "
        "describe different tools)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        "        if group is not None and group[0] == head:",
        "        if group is not None and True:",
        ("tests.unit.test_parallel_batch",),
        "a restored group is adopted by a step that does not head it (the "
        "checkpoint's group indexes are trusted blindly)",
    ),
    Mutant(
        "src/ai_assistant/agents/graph_wiring.py",
        "        with _delegation_lock:",
        "        if True:",
        ("tests.unit.test_graph_wiring_delegations",),
        "the delegation cache is read-modified-written without its lock (two "
        "branches of one group pay twice for the same classification)",
    ),
    Mutant(
        "src/ai_assistant/agents/graph_wiring.py",
        "        for tool_name, params in calls:\n"
        "            _delegation_for(tool_name, params)",
        "        return",
        ("tests.unit.test_graph_wiring_delegations",),
        "the batch is never classified before it fans out (a branch pays for "
        "the classification inside the worker instead)",
    ),
    Mutant(
        "src/ai_assistant/domain/parallel.py",
        "    return max(1, min(PARALLEL_MAX_HARD_CAP, raw))",
        "    return max(0, min(PARALLEL_MAX_HARD_CAP, raw))",
        ("tests.unit.test_plan_parallel_groups",),
        "the fan-out floor drops to zero (a configured 0 disables the batch "
        "instead of collapsing to one worker)",
    ),
    Mutant(
        "src/ai_assistant/domain/parallel.py",
        "    return max(1, min(PARALLEL_MAX_HARD_CAP, raw))",
        "    return max(1, raw)",
        ("tests.unit.test_plan_parallel_groups",),
        "the hard ceiling is not applied (a deployment can start as many "
        "branches as it configures)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/helpers.py",
        "    if len(raw) < 2:\n"
        "        return None",
        "    if False:\n"
        "        return None",
        ("tests.unit.test_plan_parallel_groups",),
        "a one-step group is accepted (a 'parallel' batch with no sibling)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/helpers.py",
        "    if indexes != sorted(set(indexes)):",
        "    if False:",
        ("tests.unit.test_plan_parallel_groups",),
        "a group's order is not checked (duplicates or a descending list run "
        "as a group and the merge order becomes ambiguous)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/helpers.py",
        "    if any(index < 0 or index >= plan_len for index in indexes):",
        "    if False:",
        ("tests.unit.test_plan_parallel_groups",),
        "a group may point outside the plan (the batch indexes a step that "
        "does not exist)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/helpers.py",
        "    if not isinstance(raw, list):",
        "    if False:",
        ("tests.unit.test_plan_parallel_groups",),
        "a non-list group is accepted (a tuple or a string reaches the batch "
        "as if the planner had declared indexes)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/helpers.py",
        "        if not isinstance(item, int) or isinstance(item, bool):",
        "        if not isinstance(item, int):",
        ("tests.unit.test_plan_parallel_groups",),
        "a JSON true passes as an index (bool is an int in Python, and it "
        "aliases step 1)",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/reason_prepare.py",
        "        if has_rag_evidence_for_step(steps, done_count):",
        "        if False:",
        ("tests.unit.test_reason_prepare_guards",),
        "the step directive demands a search even when the batch already "
        "fetched the evidence (the model re-searches and contradicts the "
        "synthesis request the tool node appended)",
    ),
    Mutant(
        "src/ai_assistant/agents/completion.py",
        '        "step_kinds": {"type": "array", "items": {',
        '        "step_kindz": {"type": "array", "items": {',
        ("tests.unit.test_completion_guard_paths",),
        "the planner grammar drops step_kinds again (the constrained decoder "
        "cannot emit the classification the prompt asks for)",
    ),
    # ── The lazy encoder load (ADR 0044 §9) ─────────────────────────────────
    Mutant(
        "src/ai_assistant/rag/embeddings.py",
        "        with self._load_lock:\n            if not self._loaded:",
        "        if True:\n            if not self._loaded:",
        ("tests.unit.test_embedding_loader",),
        "the lazy encoder load is not guarded (two rag_search branches on a "
        "cold cache both call the factory: double RAM/VRAM, one instance "
        "leaked when the second assignment overwrites the first)",
    ),
    Mutant(
        "src/ai_assistant/rag/embeddings.py",
        "            if not self._loaded:\n                self._encoder, _ = self._factory()",
        "            if True:\n                self._encoder, _ = self._factory()",
        ("tests.unit.test_embedding_loader",),
        "the double-check is dropped (every waiting branch repeats the load "
        "after the first one completes)",
    ),
    # ── The follow-up 2026-10-28 guards ──────────────────────────────────────
    Mutant(
        "src/ai_assistant/adapters/http/security.py",
        '    parts = urlsplit(value)\n'
        "    if not parts.scheme or not parts.netloc:\n"
        '        return value.rstrip("/")\n'
        '    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"',
        '    return value.rstrip("/")',
        ("tests.unit.test_http_security",),
        "the Referer is compared verbatim again (a full URL never matches the "
        "origin allow-list, so every legitimate browser request is refused)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/lifecycle.py",
        "    if memory_store is not None:\n"
        "        try:\n"
        "            memory_store.close()\n"
        "        except Exception as error:\n"
        '            logger.warning(f"Closing the long-term memory store failed: {error}")',
        "    if memory_store is not None:\n"
        "        memory_store.close()",
        ("tests.integration.test_server_module",),
        "a raising memory-store close escapes the lifespan and skips the task "
        "store close (the task connection stays open for the whole process)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/lifecycle.py",
        "        if not _drain_runtime_workers(server, platform, logger):",
        "        if False and not _drain_runtime_workers(server, platform, logger):",
        ("tests.integration.test_server_module",),
        "shutdown closes model/checkpoint/memory dependencies underneath a "
        "runtime worker that did not stop inside the bounded drain window",
    ),
    Mutant(
        "src/ai_assistant/application/tools.py",
        "        if not done.wait(timeout_seconds):",
        "        if False:",
        ("tests.unit.test_tool_gateway_guards",),
        "the declared tool deadline is ignored (a caller can block forever "
        "inside an executor)",
    ),
    Mutant(
        "src/ai_assistant/application/tools.py",
        "            if self._closing:\n"
        "                return _ExecutionOutcome(refused=True)",
        "            if False:\n"
        "                return _ExecutionOutcome(refused=True)",
        ("tests.unit.test_tool_gateway_guards",),
        "tool execution can start after shutdown closed admission",
    ),
    Mutant(
        "src/ai_assistant/application/tools.py",
        "            target=lambda: execution_context.run(invoke),",
        "            target=invoke,",
        ("tests.unit.test_tool_gateway_guards",),
        "tool worker threads lose the caller's trace/context variables",
    ),
    Mutant(
        "src/ai_assistant/application/tasks.py",
        "            if not self._workers:\n"
        "                self._close_store()\n"
        "                return True\n"
        "            return False",
        "            if not self._workers:\n"
        "                self._close_store()\n"
        "                return True\n"
        "            return True",
        ("tests.unit.test_task_lifecycle_guards", "tests.integration.test_server_module"),
        "task shutdown reports drained while a worker still owns runtime and storage",
    ),
    Mutant(
        "src/ai_assistant/domain/tools.py",
        "        if (not isinstance(self.timeout_seconds, int)\n"
        "                or isinstance(self.timeout_seconds, bool)\n"
        "                or self.timeout_seconds <= 0):",
        "        if False:",
        ("tests.unit.test_tool_gateway_guards",),
        "a zero, negative or non-integer tool timeout enters the execution contract",
    ),
    Mutant(
        "src/ai_assistant/orchestration/nodes/tool.py",
        '    if not any(result.get("outcome_unknown") is True for result in results):',
        "    if True:",
        ("tests.unit.test_tool_node_guards", "tests.unit.test_parallel_batch"),
        "an indeterminate tool outcome is allowed to continue into automatic "
        "reflection/reasoning and may retry a side effect still running",
    ),
    # ── Checkpoint lifecycle (ADR 0023) ───────────────────────────────────────
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        "        saver = stack.enter_context(factory(url))",
        "        saver = factory(url)",
        ("tests.unit.test_checkpointing",),
        "the Postgres/Redis factory's context manager is never entered (the "
        "saver is a generator context manager, setup() fails and the backend "
        "silently degrades to MemorySaver)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        "    except BaseException:\n"
        "        with suppress(Exception):\n"
        "            stack.close()\n"
        "        raise",
        "    except BaseException:\n"
        "        raise",
        ("tests.unit.test_checkpointing",),
        "a backend that connects and then fails setup() leaks its connection "
        "(the factory context manager is never exited)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        "    if directory:\n"
        "        os.makedirs(directory, exist_ok=True)",
        "    if True:\n"
        "        os.makedirs(directory, exist_ok=True)",
        ("tests.unit.test_checkpointing",),
        "a bare relative checkpoint path calls os.makedirs('') and falls back "
        "to MemorySaver (a configured SQLite backend is not durable)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/checkpointing.py",
        "        if self._closed:\n"
        "            return",
        "        if False:\n"
        "            return",
        ("tests.unit.test_checkpointing",),
        "the checkpointer handle closes its connection twice (shutdown paths "
        "may run more than once; a second close on a released handle raises)",
    ),
    Mutant(
        "src/ai_assistant/application/memory.py",
        "        except Exception as error:\n"
        '            logger.warning("Long-term memory ranking failed: %s", error)\n'
        "            return []",
        "        except KeyboardInterrupt as error:\n"
        '            logger.warning("Long-term memory ranking failed: %s", error)\n'
        "            return []",
        ("tests.unit.test_memory_service",),
        "a malformed rehydrated record escapes the ranker and fails the run "
        "(memory must never be a dependency of the run)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/lifecycle.py",
        "    if checkpointer_handle is not None:\n"
        "        try:\n"
        "            checkpointer_handle.close()\n"
        "        except Exception as error:\n"
        '            logger.warning(f"Closing the checkpoint store failed: {error}")',
        "    if False:\n"
        "        try:\n"
        "            checkpointer_handle.close()\n"
        "        except Exception as error:\n"
        '            logger.warning(f"Closing the checkpoint store failed: {error}")',
        ("tests.integration.test_server_module",),
        "the checkpoint handle is never closed on shutdown (the SQLite "
        "connection or the Postgres/Redis context manager stays open)",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/lifecycle.py",
        "    if checkpointer_handle is not None:\n"
        "        try:\n"
        "            checkpointer_handle.close()\n"
        "        except Exception as error:\n"
        '            logger.warning(f"Closing the checkpoint store failed: {error}")',
        "    if checkpointer_handle is not None:\n"
        "        checkpointer_handle.close()",
        ("tests.integration.test_server_module",),
        "a raising checkpointer close escapes the lifespan and skips the "
        "memory and task store closes",
    ),
    # ── ADR 0047: the boundaries that were still fail-open ────────────────────
    Mutant(
        "src/ai_assistant/domain/tools.py",
        "        if not isinstance(self.side_effect, SideEffect):",
        "        if False:",
        ("tests.unit.test_plugins",),
        "a side effect outside the vocabulary is accepted at construction and "
        "mapped to read_only (an unknown label widens the tool's authority)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        "            removed += self._shrink_owner(owner, share)",
        "            removed += self._shrink_owner(owner, max_bytes)",
        ("tests.unit.test_memory_store",),
        "every owner is allowed the whole byte budget (the cap is no longer "
        "shared, so one tenant can grow the store past it)",
    ),
    Mutant(
        "src/ai_assistant/adapters/persistence/memory_store.py",
        "        if not owners:\n            return 0",
        "        if False:\n            return 0",
        ("tests.unit.test_memory_store",),
        "an empty store with a byte cap divides by zero instead of reporting "
        "no evictions (the composition root's startup prune crashes)",
    ),
    Mutant(
        "src/ai_assistant/agents/short_term_memory.py",
        "            if session.owner == owner",
        "            if True",
        ("tests.unit.test_short_term_memory",),
        "eviction is global again (one tenant's session ids push out every "
        "other tenant's context)",
    ),
    Mutant(
        "src/ai_assistant/agents/service.py",
        "            short_term.record(session_id, conversation_turns(turns), owner=owner)",
        '            short_term.record(session_id, conversation_turns(turns), owner="")',
        ("tests.unit.test_short_term_memory",),
        "the facade records every tenant under one owner (the per-owner bound "
        "collapses into a single eviction pool)",
    ),
    Mutant(
        "src/ai_assistant/agents/ui_action_service.py",
        '                if request.session_id != action.get("session_id"):',
        "                if False:",
        ("tests.unit.test_agent_resume_services",),
        "a desktop ack resumes the run from any session (the request id alone "
        "is treated as the whole authorisation, unlike approve)",
    ),
    # Web search: privacy, bounded egress, and model-owned freshness routing.
    Mutant(
        "src/ai_assistant/tools/builtin/web_tools.py",
        "        if has_sensitive_content(query):",
        "        if False:",
        ("tests.unit.test_web_search",),
        "credential-shaped text is sent to the public search endpoint",
    ),
    Mutant(
        "src/ai_assistant/adapters/web_search.py",
        "        if len(payload) > 1_000_000:",
        "        if False:",
        ("tests.unit.test_web_search",),
        "an unbounded search response is accepted into model context",
    ),
    Mutant(
        "src/ai_assistant/orchestration/specialists/chatbot.py",
        '        return classify_web_freshness(query, backend) == "WEB_SEARCH"',
        "        return False",
        ("tests.unit.test_web_search",),
        "the chat route ignores the model's current-information decision",
    ),
    Mutant(
        "src/ai_assistant/orchestration/helpers.py",
        '    if kind == "web_search":\n        return {"mode": "web_search", "query": step_text}',
        '    if kind == "web_search":\n        return {"mode": "direct_answer"}',
        ("tests.unit.test_web_search",),
        "the planner routes freshness-sensitive questions back to model memory",
    ),
    # ── ADR 0055: path/logging parity, web_search hardening & shutdown bounds ─
    Mutant(
        "src/ai_assistant/bootstrap/server.py",
        "        _paths = resolve_paths(PACKAGE_ROOT)\n"
        "        logger, LOG_FILE_PATH = setup_logging(str(_paths.logs_dir))",
        "        _paths = resolve_paths(PACKAGE_ROOT)\n"
        '        logger, LOG_FILE_PATH = setup_logging(str(PACKAGE_ROOT / "logs"))',
        ("tests.integration.test_composition_root_bootstrap",),
        "composition root ignores APP_DATA_DIR for server logs",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/server.py",
        "def _create_web_search_client(settings: Any) -> Any | None:\n"
        '    """Construct the outbound search client only when web search is enabled."""\n'
        "    if not settings.enabled:\n"
        "        return None",
        "def _create_web_search_client(settings: Any) -> Any | None:\n"
        '    """Construct the outbound search client only when web search is enabled."""\n'
        "    if False:\n"
        "        return None",
        ("tests.unit.test_bootstrap_coverage_gaps", "tests.integration.test_composition_root_bootstrap"),
        "web search client is built even when AI_WEB_SEARCH_ENABLED=0",
    ),
    Mutant(
        "src/ai_assistant/adapters/web_search.py",
        '    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:',
        "    if False:",
        ("tests.unit.test_web_search",),
        "non-HTTP URL schemes are accepted by the web search endpoint",
    ),
    Mutant(
        "src/ai_assistant/adapters/web_search.py",
        "        if not parser.results and _looks_like_challenge(html):\n"
        "            raise WebSearchChallengeError",
        "        if False:\n"
        "            raise WebSearchChallengeError",
        ("tests.unit.test_web_search",),
        "bot challenge HTML silently degrades into empty web search results",
    ),
    Mutant(
        "src/ai_assistant/orchestration/specialists/chatbot.py",
        '            or not getattr(self._web_search, "enabled", True)',
        "            or False",
        ("tests.unit.test_web_search",),
        "chat invokes the freshness classifier even when web search is disabled",
    ),
    Mutant(
        "src/ai_assistant/orchestration/specialists/chatbot.py",
        "        suppress_citations = self._prompts.is_character_query(query) and not web_sources",
        "        suppress_citations = self._prompts.is_character_query(query)",
        ("tests.unit.test_web_search",),
        "character-like web queries suppress public source URL citations",
    ),
    Mutant(
        "src/ai_assistant/tools/factory.py",
        '        if name == "web_search" and not include_web_search:\n'
        "            continue",
        "        if False:\n"
        "            continue",
        ("tests.unit.test_web_search",),
        "disabled web_search tool remains registered in the agent catalog",
    ),
    Mutant(
        "src/ai_assistant/tools/tool_contract.py",
        '    "web_search": {"timeout_seconds": 30, "policy": "read_only"},',
        '    "web_search": {"timeout_seconds": 10, "policy": "read_only"},',
        ("tests.unit.test_tool_contract", "tests.unit.test_web_search"),
        "web_search contract timeout stays capped below WebSearchSettings ceiling",
    ),
    Mutant(
        "src/ai_assistant/application/tasks.py",
        '               *, owner: str = "default") -> AgentTask:\n'
        "        self._ensure_open()",
        '               *, owner: str = "default") -> AgentTask:\n'
        "        pass",
        ("tests.unit.test_task_lifecycle_guards",),
        "submit writes SUBMITTED task rows during shutdown before failing to start a worker",
    ),
    Mutant(
        "src/ai_assistant/application/tasks.py",
        '        metadata["agent_run.resume"] = dict(input_data)\n'
        "        self._ensure_open()",
        '        metadata["agent_run.resume"] = dict(input_data)\n'
        "        pass",
        ("tests.unit.test_task_lifecycle_guards",),
        "resume overwrites INPUT_REQUIRED to WORKING during shutdown before failing to start a worker",
    ),
    Mutant(
        "src/ai_assistant/application/tools.py",
        '        request = ToolRequest(tool_name, dict(parameters), correlation_id or str(uuid4()))\n'
        "        if self._is_closing():",
        '        request = ToolRequest(tool_name, dict(parameters), correlation_id or str(uuid4()))\n'
        "        if False:",
        ("tests.unit.test_tool_gateway_guards",),
        "execute spends single-use approval grants after shutdown closed admission",
    ),
    Mutant(
        "src/ai_assistant/application/tools.py",
        "            if len(self._workers) >= self._max_concurrent_workers:\n"
        "                return _ExecutionOutcome(saturated=True)",
        "            if False:\n"
        "                return _ExecutionOutcome(saturated=True)",
        ("tests.unit.test_tool_gateway_guards",),
        "lingering timed-out tool workers allow unbounded thread creation",
    ),
    Mutant(
        "src/ai_assistant/adapters/http/agent_routes.py",
        "        except BaseException as error:  # Stream worker boundary must always unblock _sse_events.",
        "        except Exception as error:",
        ("tests.unit.test_http_agent_routes",),
        "BaseException in agent SSE worker leaves the event stream blocked forever",
    ),
    Mutant(
        "src/ai_assistant/bootstrap/cli.py",
        "        log_config=None, access_log=False, timeout_graceful_shutdown=10,",
        "        log_config=None, access_log=False,",
        ("tests.integration.test_main_entry_points",),
        "uvicorn server waits indefinitely for stalled connections during shutdown",
    ),
    Mutant(
        "src/ai_assistant/observability/tracing.py",
        "        if not self._probe(self._endpoint, self._probe_timeout):",
        "        if False:",
        ("tests.unit.test_tracing_sandbox_guards",),
        "OTLP exporter skips reachability probe and invokes retry-loop on closed collector",
    ),
    Mutant(
        "src/ai_assistant/observability/tracing.py",
        "        if self._clock() < self._retry_at:",
        "        if False:",
        ("tests.unit.test_tracing_sandbox_guards",),
        "OTLP exporter ignores circuit cooldown and re-probes on every batch",
    ),
    Mutant(
        "src/ai_assistant/observability/tracing.py",
        "        if self._shutdown or not self._ensure_collector_ready():",
        "        if False:",
        ("tests.unit.test_tracing_sandbox_guards",),
        "OTLP exporter delegates even after shutdown or while collector circuit is open",
    ),
    Mutant(
        "src/ai_assistant/config/logging.py",
        "        if getattr(handler, _OWNED_HANDLER_ATTRIBUTE, False):\n            handler.close()",
        "        if False:\n            handler.close()",
        ("tests.unit.test_logging",),
        "logging reconfiguration orphans the previous owned file handler",
    ),
    Mutant(
        "src/ai_assistant/llm/hardware.py",
        '    torch_module = sys.modules.get("torch")',
        "    import torch as torch_module",
        ("tests.unit.test_hardware",),
        "ML teardown imports an unloaded Torch stack merely to release it",
    ),
    Mutant(
        "src/ai_assistant/llm/hardware.py",
        "    except Exception:\n        pass",
        "    except RuntimeError:\n        pass",
        ("tests.unit.test_hardware",),
        "a non-runtime CUDA driver failure escapes the best-effort teardown boundary",
    ),
)


#: Marks an in-flight run. Its absence means "the tree is safe to mutate": a
#: lock left behind by a killed process has to be cleared by hand *after*
#: restoring the source, which is exactly the conversation we want to force.
LOCK_PATH = BASE / "scripts" / ".mutation_check.lock"


def _pid_is_running(pid: int) -> bool:
    """Return whether ``pid`` still owns a live process, without signalling it."""
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_lock() -> None:
    """Refuse overlap, while reclaiming a PID lock whose owner has exited."""
    for _attempt in range(2):
        try:
            handle = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = int(LOCK_PATH.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                owner = -1
            # An empty/partially-written lock may belong to a process between
            # O_EXCL and write; only a complete PID is safe to reclaim.
            if owner > 0 and not _pid_is_running(owner):
                LOCK_PATH.unlink(missing_ok=True)
                continue
            message = (
                f"mutation_check: {LOCK_PATH} is owned by active PID "
                f"{owner if owner > 0 else 'unknown'}; another sweep is active"
            )
            raise SystemExit(message) from None
        os.write(handle, str(os.getpid()).encode("ascii"))
        os.close(handle)
        return
    message = "mutation_check: stale lock could not be reclaimed"
    raise SystemExit(message)


def _release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


def clear_bytecode_cache(target: Path) -> None:
    """Drop the cached bytecode of the file the gate is about to mutate.

    Python validates a ``.pyc`` by the source's **mtime (whole seconds)** and
    its **size**. A mutation that keeps the size — ``"1"`` → ``"0"`` — and is
    restored within the same second therefore leaves a *stale* bytecode cache
    that still holds the mutated code: the next import loads the mutant, and
    a test that should have failed passes. The symptom is a mutant that
    "survived" for no reason, and — worse — a tree that keeps mutated code
    after the gate is done.

    The gate must not leave mutated code behind, neither on disk nor in the
    bytecode cache.
    """
    cache = target.parent / "__pycache__"
    if not cache.is_dir():
        return
    for cached in cache.glob(f"{target.stem}.*.pyc"):
        try:
            cached.unlink()
        except OSError:
            pass


def subprocess_env(base: Path = BASE) -> dict[str, str]:
    """The environment the mutant's suite runs in.

    ``PYTHONDONTWRITEBYTECODE=1`` is not a nicety: the suite must not *write*
    a ``.pyc`` of the mutated module, and the cache is dropped before the
    run, so the import in the test process is the mutated source and not a
    stale bytecode of the original.
    """
    return {
        **os.environ,
        "PYTHONPATH": str(base / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def run_mutant(mutant: Mutant, base: Path = BASE) -> str:
    """Mutate one guard, run its tests, restore the file verbatim.

    Returns ``"KILLED"``, ``"SURVIVED"`` or ``"STALE"`` — the last when the
    anchor no longer matches the source, which fails the run rather than
    passing it silently. A suite that times out is a crash, unless the mutant
    explicitly opts into ``kill_on_timeout`` (its guard turns "fail" into
    "never finish", and the budget is set far above the unmutated runtime).
    """
    target = base / mutant.file
    original = target.read_text(encoding="utf-8")
    if original.count(mutant.original) != 1:
        print(f"  STALE {mutant.label}: anchor not unique/absent in {mutant.file}")
        return "STALE"
    try:
        target.write_text(original.replace(mutant.original, mutant.mutated), encoding="utf-8")
        # The mutated module must be the one the suite imports: a stale
        # ``.pyc`` (same size, same second) would import the *original* code
        # and report the mutant as survived. The cache is dropped and the
        # suite runs without writing bytecode, so the mutation is what is
        # measured and nothing mutated is left behind.
        clear_bytecode_cache(target)
        env = subprocess_env(base)
        try:
            outcome = subprocess.run(
                [sys.executable, "-m", "unittest", *mutant.tests, "-q"],
                cwd=base, capture_output=True, text=True, timeout=mutant.timeout_seconds, env=env,
            )
        except subprocess.TimeoutExpired:
            if not mutant.kill_on_timeout:
                raise
            print(f"  TIMEOUT {mutant.label}: suite never finished in "
                  f"{mutant.timeout_seconds}s — the guard's absence hangs it")
            return "KILLED"
    finally:
        target.write_text(original, encoding="utf-8")
        clear_bytecode_cache(target)
        if target.read_text(encoding="utf-8") != original:
            message = (
                f"{mutant.file} was not restored byte-for-byte; restore it "
                "before running anything against this tree."
            )
            raise RuntimeError(message)
    if outcome.returncode == 0:
        # A survivor is the most expensive kind of answer: it means the suite
        # did not notice. Print *what the suite did* — a suite that ran zero
        # tests, or one that never imported the mutated module, looks exactly
        # like a suite that ran and agreed with the mutation.
        print(f"    (the suite stayed green: {outcome.stdout.strip()[-600:]!r})")
        return "SURVIVED"
    return "KILLED"


def floor_violation(mutant_count: int) -> str | None:
    """The reason the declared mutant count is below the floor, if it is.

    Without a floor, *deleting* a mutant is indistinguishable from *passing*
    it: the suite stays green, the coverage floor stays at 100 %, and the
    guard silently stops being guarded. ADR 0032 makes the count a gate — a
    new load-bearing decision adds a mutant in the same commit, and a removed
    one lowers this number in the same commit, so the diff always shows it.
    """
    if mutant_count < MIN_MUTANTS:
        return (
            f"{mutant_count} mutants declared, {MIN_MUTANTS} required — "
            "a guard was removed without a decision"
        )
    return None


#: Files that carry a load-bearing decision and therefore must declare at
#: least one mutant. ``MIN_MUTANTS`` counts mutants globally; this list makes
#: a *file* that quietly loses its coverage visible (the C++ gate's
#: ``PINNED_HEADERS`` shape). Add a file in the same commit that adds the
#: decision; never remove an entry to make the gate pass.
PINNED_DECISION_FILES: tuple[str, ...] = (
    "src/ai_assistant/adapters/a2a_client.py",
    "src/ai_assistant/adapters/a2a_protocol.py",
    "src/ai_assistant/adapters/http/agent_routes.py",
    "src/ai_assistant/adapters/http/health_routes.py",
    "src/ai_assistant/adapters/http/security.py",
    "src/ai_assistant/adapters/persistence/checkpointing.py",
    "src/ai_assistant/adapters/persistence/memory_store.py",
    "src/ai_assistant/adapters/persistence/sqlite_store.py",
    "src/ai_assistant/adapters/web_search.py",
    "src/ai_assistant/agents/approval_service.py",
    "src/ai_assistant/agents/context.py",
    "src/ai_assistant/agents/response.py",
    "src/ai_assistant/agents/service.py",
    "src/ai_assistant/agents/short_term_memory.py",
    "src/ai_assistant/agents/ui_action_service.py",
    "src/ai_assistant/application/memory.py",
    "src/ai_assistant/application/tasks.py",
    "src/ai_assistant/application/tools.py",
    "src/ai_assistant/bootstrap/app_factory.py",
    "src/ai_assistant/bootstrap/cli.py",
    "src/ai_assistant/bootstrap/lifecycle.py",
    "src/ai_assistant/bootstrap/server.py",
    "src/ai_assistant/bootstrap/server_manager.py",
    "src/ai_assistant/config/logging.py",
    "src/ai_assistant/domain/tools.py",
    "src/ai_assistant/llm/hardware.py",
    "src/ai_assistant/observability/metrics.py",
    "src/ai_assistant/observability/tracing.py",
    "src/ai_assistant/orchestration/nodes/tool.py",
    "src/ai_assistant/orchestration/specialists/chatbot.py",
    "src/ai_assistant/rag/embeddings.py",
    "src/ai_assistant/settings.py",
    "src/ai_assistant/tools/factory.py",
    "src/ai_assistant/tools/sandbox.py",
    "src/ai_assistant/tools/tool_contract.py",
    "src/ai_assistant/tools/builtin/web_tools.py",
)


def pinned_file_violations(
    mutants: tuple[Mutant, ...] = MUTANTS,
) -> list[str]:
    """Pinned files that declare no mutant — the per-file floor.

    ``MIN_MUTANTS`` proves the *total* did not shrink; it cannot see one file
    lose its last mutant while another file gains two. The list names the
    files whose decision is load-bearing, so the gate fails on the file, not
    only on the sum.
    """
    covered = {mutant.file for mutant in mutants}
    return [path for path in PINNED_DECISION_FILES if path not in covered]


def dirty_tree_violations(
    mutants: tuple[Mutant, ...] = MUTANTS,
) -> list[Mutant]:
    """Anchors that are not present exactly once *before* the run starts.

    A gate killed mid-mutant (Ctrl-C, a machine restart) restores nothing: the
    next run would measure the remaining mutants against a tree that still
    holds one — false kills, and one "stale" report for what is really a
    leftover. Refuse to start on a dirty tree instead of measuring it. The
    check is the same anchor count ``run_mutant`` performs, just applied to the
    whole tuple up front.
    """
    return [
        mutant for mutant in mutants
        if (BASE / mutant.file).read_text(encoding="utf-8").count(mutant.original) != 1
    ]


def select_mutants(files: Sequence[str] | None) -> tuple[Mutant, ...]:
    """Return the requested file subset, or the complete inventory."""
    if not files:
        return MUTANTS
    requested = {
        item.strip().replace("\\", "/")
        for value in files
        for item in value.split(",")
        if item.strip()
    }
    known = {mutant.file for mutant in MUTANTS}
    unknown = sorted(requested - known)
    if unknown:
        message = "unknown mutation file(s): " + ", ".join(unknown)
        raise ValueError(message)
    return tuple(mutant for mutant in MUTANTS if mutant.file in requested)


def _sandbox_ignore(_directory: str, names: list[str]) -> set[str]:
    """Generated/heavy checkout entries a mutation worker never needs."""
    ignored = {name for name in names if name in _SANDBOX_IGNORED_NAMES}
    ignored.update(name for name in names if name.endswith((".sqlite", ".sqlite-shm", ".sqlite-wal")))
    return ignored


def create_sandbox(worker_root: Path) -> Path:
    """Copy the authored Python package and tiny cross-language contracts."""
    sandbox_base = worker_root / "AIAssistant"
    shutil.copytree(BASE, sandbox_base, ignore=_sandbox_ignore)
    for relative in _OUTER_CONTRACT_FILES:
        source = REPOSITORY_ROOT / relative
        if not source.is_file():
            continue
        destination = worker_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return sandbox_base


def partition_mutants(
    indexed: Sequence[tuple[int, Mutant]],
    jobs: int,
) -> list[list[tuple[int, Mutant]]]:
    """Balance work while keeping identical owning suites on one worker."""
    grouped: dict[tuple[str, ...], list[tuple[int, Mutant]]] = defaultdict(list)
    for item in indexed:
        grouped[item[1].tests].append(item)
    partitions: list[list[tuple[int, Mutant]]] = [[] for _ in range(jobs)]
    loads = [0] * jobs
    for group in sorted(grouped.values(), key=len, reverse=True):
        target = min(range(jobs), key=loads.__getitem__)
        partitions[target].extend(group)
        loads[target] += len(group)
    return [partition for partition in partitions if partition]


def _baseline_suite(
    tests: tuple[str, ...],
    timeout_seconds: int,
    base: Path,
) -> str | None:
    """Return a diagnostic when an unmodified owning suite is not green."""
    try:
        outcome = subprocess.run(
            [sys.executable, "-m", "unittest", *tests, "-q"],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=subprocess_env(base),
        )
    except subprocess.TimeoutExpired:
        return f"baseline timed out after {timeout_seconds}s: {', '.join(tests)}"
    if outcome.returncode == 0:
        return None
    detail = (outcome.stdout + "\n" + outcome.stderr).strip()[-2000:]
    return f"baseline failed: {', '.join(tests)}\n{detail}"


def _run_partition(
    partition: list[tuple[int, Mutant]],
    total: int,
    worker_number: int,
) -> tuple[list[tuple[int, Mutant, str]], list[str]]:
    """Run one balanced partition in one disposable checkout copy."""
    with tempfile.TemporaryDirectory(prefix=f"aia-mutation-{worker_number}-") as directory:
        base = create_sandbox(Path(directory))
        baseline_errors: list[str] = []
        suites: dict[tuple[str, ...], int] = {}
        for _index, mutant in partition:
            suites[mutant.tests] = max(suites.get(mutant.tests, 0), mutant.timeout_seconds)
        for tests, timeout_seconds in suites.items():
            diagnostic = _baseline_suite(tests, timeout_seconds, base)
            if diagnostic:
                baseline_errors.append(diagnostic)
        if baseline_errors:
            return [], baseline_errors

        results: list[tuple[int, Mutant, str]] = []
        for index, mutant in partition:
            verdict = run_mutant(mutant, base)
            print(
                f"[{index:3d}/{total}] {verdict:9s} "
                f"{mutant.file.split('/')[-1]}: {mutant.label}",
                flush=True,
            )
            results.append((index, mutant, verdict))
        return results, []


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files",
        nargs="+",
        help="Run mutants for these exact repository-relative files (comma-separated also accepted)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Number of isolated mutation workers (default: {DEFAULT_JOBS})",
    )
    return parser.parse_args(argv)


def _execute(selected: tuple[Mutant, ...], jobs: int) -> int:
    """Run a validated mutation selection and report its verdict."""
    pinned = pinned_file_violations()
    if pinned:
        print("mutation_check: pinned decision files without a mutant:")
        for path in pinned:
            print(f"  {path}")
        return 1
    _acquire_lock()
    try:
        # Only meaningful under the lock: an in-flight run is *supposed* to
        # have one mutant applied. Held by us, a dirty anchor means the
        # previous run was killed and restored nothing.
        dirty = dirty_tree_violations()
        if dirty:
            print("mutation_check: the tree still holds a mutant (a previous run was killed):")
            for mutant in dirty:
                print(f"  {mutant.file}: {mutant.label}")
            print("  restore those files from git before measuring again.")
            return 1
        indexed = list(enumerate(selected, start=1))
        partitions = partition_mutants(indexed, min(jobs, len(indexed)))
        results: list[tuple[int, Mutant, str]] = []
        baseline_errors: list[str] = []
        with ThreadPoolExecutor(max_workers=len(partitions)) as pool:
            futures = {
                pool.submit(_run_partition, partition, len(selected), worker_number): worker_number
                for worker_number, partition in enumerate(partitions, start=1)
            }
            for future in as_completed(futures):
                worker_results, worker_errors = future.result()
                results.extend(worker_results)
                baseline_errors.extend(worker_errors)
        if baseline_errors:
            print("mutation_check: unmodified owning suite is not green:")
            for diagnostic in baseline_errors:
                print(f"  {diagnostic}")
            return 1

        survivors: list[str] = []
        stale: list[str] = []
        for _index, mutant, verdict in sorted(results):
            entry = f"{mutant.file}: {mutant.label} (tests: {', '.join(mutant.tests)})"
            if verdict == "SURVIVED":
                survivors.append(entry)
            elif verdict == "STALE":
                stale.append(entry)
    finally:
        _release_lock()
    killed = len(selected) - len(survivors) - len(stale)
    print(
        f"\nMutants: {len(selected)}  killed: {killed}  "
        f"survived: {len(survivors)}  stale: {len(stale)}"
    )
    for entry in survivors:
        print(f"  SURVIVED -> {entry}")
    for entry in stale:
        print(f"  STALE    -> {entry} (anchor no longer matches the source)")
    if len(MUTANTS) < MIN_MUTANTS:
        # ADR 0032: the count itself is a gate. A mutant that disappeared
        # without a decision is exactly the drift this gate exists to catch.
        print(f"  FLOOR    -> {floor_violation(len(MUTANTS))}")
        return 1
    return 1 if survivors or stale else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.jobs < 1:
        print("mutation_check: --jobs must be at least 1")
        return 2
    try:
        selected = select_mutants(args.files)
    except ValueError as error:
        print(f"mutation_check: {error}")
        return 2
    return _execute(selected, args.jobs)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
