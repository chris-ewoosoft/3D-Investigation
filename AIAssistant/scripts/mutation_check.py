"""Mutation check for the load-bearing guards (ADR 0015 / 0011 / 0018 / 0020).

100% statement/branch coverage says the guards are *executed*, not that they
are *correct*: a test that asserts the wrong direction still covers the line.
This script inverts one decision at a time in each guard and runs the tests
that own it. A mutant is KILLED when the suite fails (the guard's behaviour is
pinned) and SURVIVES when it stays green (the test would not notice the guard
being disabled).

Each mutant is written into the real source file and restored in a ``finally``
that re-reads the file and verifies it came back byte-identical. A lock file
keeps two runs from overlapping, and its presence after a crash refuses to start
again until the source is restored by hand. Run it *alone* — never in the same
step as the suite, which reads exactly these files; CI runs it after the tests
in ``Build-Python-ci.yml``.

An anchor that no longer matches the source counts as STALE and fails the run:
the check may not go green by mutating code that is not there.

Usage (from AIAssistant/):  PYTHONPATH=src python scripts/mutation_check.py
Exit code 0 = every mutant killed and every anchor present; 1 otherwise.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


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
        "return f\"Sandbox: subcommand '{subcmd}' is not in the {executable} allow-list.\"",
        "return None",
        ("tests.unit.test_sandbox",),
        "unknown subcommand allowed",
    ),
    Mutant(
        "src/ai_assistant/tools/sandbox.py",
        "if head in _GIT_ESCAPE_FLAGS:",
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
        "if target_name in _DENIED_WRITE_PATHS or any(",
        "if False:",
        ("tests.unit.test_sandbox",),
        "write-deny on .env/policy files removed",
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
        "if _path_escapes_workspace(argument, cwd):\n            return f\"Sandbox: {executable} script path must stay inside the workspace.\"",
        "if False:\n            return f\"Sandbox: {executable} script path must stay inside the workspace.\"",
        ("tests.unit.test_sandbox",),
        "python/pytest script outside the workspace allowed",
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
    # ── RAG cache seal fail-closed ───────────────────────────────────────────
    Mutant(
        "src/ai_assistant/rag/cache_integrity.py",
        "if key is None or not recorded:\n        return False",
        "if key is None or not recorded:\n        return True",
        ("tests.unit.test_rag_cache_integrity", "tests.unit.test_index"),
        "unverifiable cache trusted (fail-open)",
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
    # ── Transport policy: the MCP mount fails closed (ADR 0008) ──────────────
    Mutant(
        "src/ai_assistant/adapters/http/security.py",
        "return not _is_loopback(host) and not policy.mcp_token",
        "return False",
        ("tests.unit.test_http_security",),
        "public bind without AI_MCP_TOKEN left exposed",
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
        "if has_sensitive_keys(params):",
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
        "with self._session_lock(request.session_id):\n            return self._approval_service.approve(request)",
        "return self._approval_service.approve(request)",
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
        ("tests.unit.test_a2a_client",),
        "A2A redirects followed (SSRF behind the endpoint allowlist)",
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
        "if _COMPOSED:\n            return\n        _COMPOSED = True",
        "_COMPOSED = True",
        ("tests.integration.test_composition_root_bootstrap",),
        "second compose() rebuilds the graph (duplicate handlers and routers)",
        timeout_seconds=180,
        kill_on_timeout=True,
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
        "if saver is not None:\n            return saver\n    except Exception as error:",
        "if saver is not None:\n            return saver\n    except KeyboardInterrupt as error:",
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
)


#: Marks an in-flight run. Its absence means "the tree is safe to mutate": a
#: lock left behind by a killed process has to be cleared by hand *after*
#: restoring the source, which is exactly the conversation we want to force.
LOCK_PATH = BASE / "scripts" / ".mutation_check.lock"


def _acquire_lock() -> None:
    """Refuse to overlap another run (or a crashed one still holding it)."""
    try:
        handle = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        message = (
            f"mutation_check: {LOCK_PATH} exists — another run is active, or a "
            "previous one was killed mid-mutation. Verify the source is intact "
            "(git status / git diff), restore it if not, then delete the lock. "
            "Never run this alongside the test suite."
        )
        raise SystemExit(message) from None
    os.write(handle, str(os.getpid()).encode("ascii"))
    os.close(handle)


def _release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


def run_mutant(mutant: Mutant) -> str:
    """Mutate one guard, run its tests, restore the file verbatim.

    Returns ``"KILLED"``, ``"SURVIVED"`` or ``"STALE"`` — the last when the
    anchor no longer matches the source, which fails the run rather than
    passing it silently. A suite that times out is a crash, unless the mutant
    explicitly opts into ``kill_on_timeout`` (its guard turns "fail" into
    "never finish", and the budget is set far above the unmutated runtime).
    """
    target = BASE / mutant.file
    original = target.read_text(encoding="utf-8")
    if original.count(mutant.original) != 1:
        print(f"  STALE {mutant.label}: anchor not unique/absent in {mutant.file}")
        return "STALE"
    try:
        target.write_text(original.replace(mutant.original, mutant.mutated), encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": "src"}
        try:
            outcome = subprocess.run(
                [sys.executable, "-m", "unittest", *mutant.tests, "-q"],
                cwd=BASE, capture_output=True, text=True, timeout=mutant.timeout_seconds, env=env,
            )
        except subprocess.TimeoutExpired:
            if not mutant.kill_on_timeout:
                raise
            print(f"  TIMEOUT {mutant.label}: suite never finished in "
                  f"{mutant.timeout_seconds}s — the guard's absence hangs it")
            return "KILLED"
    finally:
        target.write_text(original, encoding="utf-8")
        if target.read_text(encoding="utf-8") != original:
            message = (
                f"{mutant.file} was not restored byte-for-byte; restore it "
                "before running anything against this tree."
            )
            raise RuntimeError(message)
    return "KILLED" if outcome.returncode != 0 else "SURVIVED"


def main() -> int:
    survivors: list[str] = []
    stale: list[str] = []
    _acquire_lock()
    try:
        for index, mutant in enumerate(MUTANTS, start=1):
            verdict = run_mutant(mutant)
            print(
                f"[{index:2d}/{len(MUTANTS)}] {verdict:9s} "
                f"{mutant.file.split('/')[-1]}: {mutant.label}"
            )
            entry = f"{mutant.file}: {mutant.label} (tests: {', '.join(mutant.tests)})"
            if verdict == "SURVIVED":
                survivors.append(entry)
            elif verdict == "STALE":
                stale.append(entry)
    finally:
        _release_lock()
    killed = len(MUTANTS) - len(survivors) - len(stale)
    print(
        f"\nMutants: {len(MUTANTS)}  killed: {killed}  "
        f"survived: {len(survivors)}  stale: {len(stale)}"
    )
    for entry in survivors:
        print(f"  SURVIVED -> {entry}")
    for entry in stale:
        print(f"  STALE    -> {entry} (anchor no longer matches the source)")
    return 1 if survivors or stale else 0


if __name__ == "__main__":
    sys.exit(main())
