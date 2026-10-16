"""FastAPI router and entry point for the 3D-Reconstruction AI server.

This file is the composition root (bootstrap) that wires together all
HTTP adapters, modules, and services. Business logic lives in sub-modules:

  - /admin/*            → src/ai_assistant/adapters/http/admin_routes.py
  - /v1/chat/*          → src/ai_assistant/adapters/http/chat_routes.py
  - /health, /metrics   → src/ai_assistant/adapters/http/health_routes.py
  - /v1/agent/*         → src/ai_assistant/adapters/http/agent_routes.py (injected AgentService)
  - /a2a/*              → src/ai_assistant/adapters/a2a_router.py
  - /mcp                → src/ai_assistant/adapters/mcp.py (mounted behind the
                          MCP transport guard, least-privilege principal)

Composition is explicit and lazy. Importing this module only defines it — no
log file is opened, no config is read, no ``FastAPI`` app is built and asyncio
is left alone, and no third-party framework is imported: FastAPI, LangGraph, llama.cpp and the MCP SDK all load inside :func:`compose()` (or the helper that needs them), so importing this module pulls only the standard library and ``ai_assistant.settings``. The whole object graph (logging, model registry, tool catalog,
container, services, app, routers) is built once by :func:`compose`, which
runs on first access of a composed name (module ``__getattr__``), from
``main()``/``bootstrap_runtime()``, or when called directly.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator

# Ensure package root is on path before any ai_assistant import. Both inserts
# fire only when the file is launched from a source tree without PYTHONPATH;
# every in-process import (the test suite included) already carries both
# dirs, so the branches are unreachable here — declared exclusions, not
# unmeasured code.
_PACKAGE_ROOT = str(Path(__file__).resolve().parents[3])
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)  # pragma: no cover - source-launch bootstrap
_SRC_DIR = str(Path(__file__).resolve().parents[2])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)  # pragma: no cover - source-launch bootstrap

from ai_assistant.settings import (
    agent_bind_host,
    agent_bind_port,
    apply_startup_environment_defaults,
    load_local_dotenv,
    settings_config_dir,
    vision_llm_enabled,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ai_assistant.adapters.http.security import TransportPolicy
    from ai_assistant.adapters.mcp import McpToolServer
    from ai_assistant.agents.service import AgentService
    from ai_assistant.application.agent_runs import AgentRunService
    from ai_assistant.bootstrap.container import PlatformContainer
    from ai_assistant.config import AppPaths, ModelRegistry, RAGSettings
    from ai_assistant.domain.tasks import AgentTask
    from ai_assistant.orchestration.specialists import ChatbotAgent
    from ai_assistant.tools import ToolRegistry

# The composition root owns the process environment (ADR 0001): merge ``.env``
# and apply the library-quieting defaults *before* the imports below read the
# environment — the A2A banner and the transport policy both do so at import
# time. Without this step the file was parsed by tests only and the running
# server never saw the tokens an operator put there (LANGSMITH_API_KEY, …).
load_local_dotenv(Path(__file__).resolve().parents[3])  # project root (``.env.example``'s directory)
apply_startup_environment_defaults()


def _ensure_package_root_on_path(package_root: str) -> None:
    """Make ``python bootstrap/server.py`` work from any working directory."""
    if package_root not in sys.path:
        sys.path.insert(0, package_root)


def _silence_connection_reset(transport_cls: type) -> None:
    """Wrap ``_call_connection_lost`` so an abrupt client reset is not an error.

    Windows' proactor event loop raises ``ConnectionResetError`` from the pipe
    shutdown callback after a client disconnects abruptly; the traceback is
    noise, so the wrapper swallows exactly that exception.
    """
    original = transport_cls._call_connection_lost  # type: ignore[attr-defined]

    def _silenced(self: Any, exc: Exception) -> None:
        try:
            original(self, exc)
        except ConnectionResetError:
            pass

    transport_cls._call_connection_lost = _silenced  # type: ignore[attr-defined]


def _patch_proactor_transport() -> None:
    """Install the Windows proactor pipe-reset shim (no-op on POSIX)."""
    if sys.platform != "win32":
        return
    from asyncio.proactor_events import _ProactorBasePipeTransport

    _silence_connection_reset(_ProactorBasePipeTransport)


_ensure_package_root_on_path(str(Path(__file__).resolve().parents[2]))

# ── Canonical module-level state (replaces legacy config import) ───────────

_SERVER_START_TIME = time.monotonic()
BASE_DIR = str(Path(__file__).resolve().parents[2])
# ``BASE_DIR`` (…/AIAssistant/src) is the data/model root the legacy resolvers
# expect. The *tracked* deployment assets — ``config/*.toml`` and the ``logs/``
# directory the Qt server manager tails — live one level up in the package
# root; reading them from ``src/`` silently found nothing.
PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def _resolve_model_idx(argv: list[str], models: list[dict[str, Any]]) -> int:
    """Resolve ``-1``/``99999``/non-numeric model selectors to a safe index.

    Kept as a function (not an inline guard) so the clamping is exercised by a
    test instead of being excluded from coverage.
    """
    try:
        idx = int(argv[1]) if len(argv) > 1 else 0
    except (ValueError, IndexError):
        return 0
    if idx < 0 or idx >= len(models):
        return 0
    return idx


# ── Compatibility wrappers (replaces legacy llm_module / rag_module shims) ──


class _LLMModule:
    llm: Any = None
    llm_lock: Any = threading.RLock()
    active_model_desc: str = ""
    is_vision_model: bool = False

    @staticmethod
    def _adopt_backend(backend: Any) -> bool:
        """Bind a freshly loaded backend to the module state.

        Returns ``False`` (and logs) when the backend is missing, so a failed
        load is visible instead of leaving the previous model advertised as
        current with neither branch excluded from coverage.
        """
        compose()
        if backend is None:
            logger.error("No inference backend after load; keeping previous module state")
            return False
        _LLMModule.llm = backend
        _LLMModule.is_vision_model = getattr(backend, "is_vision_supported", False)
        _LLMModule.active_model_desc = getattr(backend, "model_description", "")
        return True

    @staticmethod
    def load_model(model_idx: int | None = None) -> None:
        compose()
        from ai_assistant.llm import get_backend, load_model
        from ai_assistant.llm.defaults import LLM_N_CTX

        load_model(
            registry=_registry,
            models_dir=str(_paths.models_dir),
            model_idx=model_idx or MODEL_IDX,
            enable_vision=USE_VISION_LLM,
            n_ctx=LLM_N_CTX,
        )
        _LLMModule._adopt_backend(get_backend())

    @staticmethod
    def reload_model() -> None:
        compose()
        from ai_assistant.llm import get_backend, reload_model
        from ai_assistant.llm.defaults import LLM_N_CTX

        reload_model(
            registry=_registry,
            models_dir=str(_paths.models_dir),
            model_idx=MODEL_IDX,
            enable_vision=USE_VISION_LLM,
            n_ctx=LLM_N_CTX,
        )
        _LLMModule._adopt_backend(get_backend())

    @staticmethod
    def estimate_tokens(text: str) -> int:
        from ai_assistant.llm.defaults import CHARS_PER_TOKEN

        # Simple approximation; full logic stays in llm module
        return max(1, int(len(text) / CHARS_PER_TOKEN))


class _RAGModule:
    rag_lock = threading.RLock()
    knowledge_index = None
    knowledge_chunks: list[Any] = []
    bm25_index = None
    embed_model_ref: Any = None
    _reranker = None
    _pipeline: Any = None     # the live retrieval handle (/health and admin reset it)
    CHUNK_CHARS = 1200
    MAX_CONTEXT_CHARS = 9000

    @staticmethod
    def _build_retrievers(index: Any = None, bm25: Any = None,
                          chunks: list[dict[str, Any]] | None = None,
                          encoder: Any = None, settings: Any = None) -> tuple[Any | None, Any | None]:
        """Build the dense/sparse retriever pair for the current index state."""
        from ai_assistant.rag.retrievers import build_retrievers
        if index is None or chunks is None or settings is None:
            return (None, None)
        return build_retrievers(index, bm25, chunks, encoder, settings)

    @staticmethod
    def _reset_index_state() -> None:
        """Drop every retrieval handle so an empty state is observable everywhere."""
        rag_module.knowledge_index = None
        rag_module.knowledge_chunks = []
        rag_module.bm25_index = None
        rag_module._pipeline = None

    @staticmethod
    def initialize_rag(enable_reranker: bool = True, force_rebuild: bool = False) -> int:
        """Load (or rebuild) the RAG index and wire the retrieval pipeline.

        ADR 0015 — the returned number is the count of chunks actually wired:
        cache hit → load; cache miss → scan + build + persist; no documents or
        any error → observable empty state (``/health`` reports
        ``rag_chunks == 0`` and degrades) with the reason logged with traceback.
        """
        try:
            count = _RAGModule._wire_index(enable_reranker, force_rebuild)
        except Exception:
            # Fail safe *and* visible: the traceback is logged, the state is
            # cleared and /health degrades instead of pretending success.
            logger.exception("RAG initialization failed; retrieval stays disabled")
            _RAGModule._reset_index_state()
            return 0
        return count

    @staticmethod
    def _wire_index(enable_reranker: bool, force_rebuild: bool) -> int:
        """Build/load the index and publish it on ``rag_module`` (may raise)."""
        from ai_assistant.config.paths import get_paths
        from ai_assistant.config.rag_config import RAGSettings
        from ai_assistant.rag.embeddings import lazy_embedding_encoder
        from ai_assistant.rag.index import (
            is_cache_valid,
            load_cache,
            load_or_build_index,
            scan_documents,
        )
        from ai_assistant.rag.loaders import DocumentLoaderRegistry
        from ai_assistant.rag.retrieval import RetrievalPipeline

        settings = RAGSettings.from_env_and_dict({})
        if not settings.enabled:
            logger.info("RAG disabled by AI_ENABLE_RAG")
            _RAGModule._reset_index_state()
            return 0
        paths = get_paths()
        encoder = lazy_embedding_encoder(settings)

        index = chunks = bm25 = None
        if not force_rebuild and is_cache_valid(paths, settings):
            index, chunks, bm25 = load_cache(paths, settings)

        if index is None:
            registry = DocumentLoaderRegistry.create_default(
                settings.chunk_chars, settings.overlap_chars)
            scanned = scan_documents(paths, registry)
            if not scanned:
                logger.warning("RAG: no documents found under %s — index not built",
                               paths.project_root)
                _RAGModule._reset_index_state()
                return 0
            # Cache is missing/stale: force the build path so the fs hash is
            # computed once (scan already happened above).
            index, chunks, bm25 = load_or_build_index(
                scanned, encoder, paths, settings, force_rebuild=True)

        if index is None or not chunks:
            logger.warning("RAG: index unavailable after load/build")
            _RAGModule._reset_index_state()
            return 0

        from typing import cast
        vector_retriever, bm25_retriever = _RAGModule._build_retrievers(
            index, bm25, cast("list[dict[str, Any]]", chunks), encoder, settings)
        pipeline = RetrievalPipeline(
            vector_retriever=vector_retriever,
            bm25_retriever=bm25_retriever,
            # The cross-encoder is an optional quality boost; it stays None
            # until a loader exists (pipeline treats None as a no-op).
            reranker=getattr(rag_module, "_reranker", None),
            knowledge_chunks=chunks,
            max_context_chars=settings.max_context_chars,
            similarity_threshold=settings.similarity_threshold,
            use_reranker=bool(enable_reranker and settings.use_reranker),
            reranker_top_k=settings.reranker_top_k,
        )
        with rag_module.rag_lock:
            rag_module.knowledge_index = index
            rag_module.knowledge_chunks = list(chunks)
            rag_module.bm25_index = bm25
            rag_module._pipeline = pipeline
            rag_module.embed_model_ref = encoder
        count = len(chunks)
        logger.info("RAG ready: %d chunks (vector=%s bm25=%s)",
                    count, vector_retriever is not None, bm25_retriever is not None)
        return count

    @staticmethod
    def get_context(query: str, query_image_b64: str | None = None, result_k: int | None = None) -> tuple[str, str, list[Any]]:
        pipeline = getattr(rag_module, "_pipeline", None)
        if pipeline is None:
            return ("", "", [])
        try:
            doc_ctx, code_ctx, image_chunks = pipeline.get_context(query, query_image_b64, result_k)
        except Exception:
            logger.exception("RAG retrieval failed")
            return ("", "", [])
        return doc_ctx, code_ctx, image_chunks

    @staticmethod
    def _release_ml_memory() -> None:
        # Protocol compliance for RAGModuleLike; delegates to vision release
        _RAGModule.release_embedding_for_vision()

    @staticmethod
    def release_embedding_for_vision() -> None:
        from ai_assistant.llm.hardware import release_ml_memory
        release_ml_memory()


# The inference defaults live in ``ai_assistant.llm.defaults`` and load with
# the backend inside :func:`compose()` and the helpers below — never at import.


# ── Global mutable chatbot agent (recreated on model/RAG reload) ─────────────
# Wrapped in a getter so the HTTP router captures the getter reference, not the
# agent instance — this allows hot-reload without re-registering the router.
_chatbot_agent_holder: list[ChatbotAgent] = []


def _get_chatbot_agent() -> ChatbotAgent:
    compose()
    from ai_assistant.orchestration.specialists import ChatbotAgent

    if not _chatbot_agent_holder:
        _chatbot_agent_holder.append(ChatbotAgent(llm_module, rag_module))
    return _chatbot_agent_holder[0]


def _rebuild_chatbot_agent() -> None:
    compose()
    from ai_assistant.orchestration.specialists import ChatbotAgent

    _chatbot_agent_holder.clear()
    _chatbot_agent_holder.append(ChatbotAgent(llm_module, rag_module))


# ── Bootstrap services ────────────────────────────────────────────────────────
def _execute_a2a_task(task: AgentTask) -> dict[str, Any]:
    """Run the shared LangGraph agent engine inside the durable A2A lifecycle."""
    compose()
    if llm_module.llm is None:
        msg = "LLM is not initialized"
        raise RuntimeError(msg)
    return _agent_run_service.run(task)


# _chatbot_agent_holder stays import-time (a plain list): only the graph it
# feeds — tool catalog, container, services, app, routers — lives in compose().


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    try:
        from ai_assistant.adapters.persistence.checkpointing import cleanup_old_checkpoints
        cleanup_old_checkpoints()
    except Exception as e:
        logger.warning(f"Lỗi dọn dẹp checkpoint: {e}")

    total = time.monotonic() - _SERVER_START_TIME
    logger.info("Server ready in %.1fs — http://127.0.0.1:8080", total)
    print(f"[SUCCESS] AI Server started successfully ({total:.1f}s)", flush=True)
    try:
        if not platform.settings.enable_mcp:
            # MCP disabled by deployment profile: still yield, otherwise the
            # asynccontextmanager would raise "generator didn't yield" and the
            # server could not start with enable_mcp = false.
            logger.info("MCP endpoint disabled by configuration")
            yield
        elif mcp_server.available:
            async with mcp_server.lifespan():
                yield
        else:
            logger.warning("MCP SDK is not installed; MCP endpoint is unavailable")
            yield
    finally:
        platform.tasks.close()
        logger.info("Server shutdown after %.1fs", time.monotonic() - _SERVER_START_TIME)


# ── FastAPI Application ───────────────────────────────────────────────────────
# Built in compose(): create_app + transport policy + router mounting.


def _refresh_agent_routes() -> None:
    """Reset agent state and schema cache on reload without modifying route table."""
    compose()
    agent_service.reset_state()
    app.openapi_schema = None


def _register_optional_protocols(
    app: FastAPI,
    platform: PlatformContainer,
    mcp_server: McpToolServer,
    transport_policy: TransportPolicy,
) -> None:
    """Wire the A2A and MCP surfaces according to the deployment profile.

    Dependency inversion (ADR 0002): the supervisor never imports the A2A
    adapter; the composition root installs its remote-specialist resolver.
    """
    compose()
    from ai_assistant.adapters.a2a import build_a2a_router
    from ai_assistant.adapters.a2a_protocol import resolve_remote_specialist
    from ai_assistant.adapters.http.security import (
        mcp_exposed_without_token,
        mcp_guarded_app,
    )
    from ai_assistant.orchestration.supervisor import set_remote_specialist_lookup

    if platform.settings.enable_a2a:
        set_remote_specialist_lookup(resolve_remote_specialist)
        app.include_router(build_a2a_router(
            platform.tasks, "3D-Reconstruction AI Assistant", "3.0.0",
            policy=transport_policy,
        ))
    else:
        set_remote_specialist_lookup(None)
    if platform.settings.enable_mcp and mcp_server.available:
        if mcp_exposed_without_token(agent_bind_host(), transport_policy):
            # Fail closed (ADR 0008): an exposed bind without AI_MCP_TOKEN
            # leaves nothing for the guard to authenticate, so the surface
            # stays unmounted and the reason is logged at startup rather than
            # surfacing as anonymous 403s from every caller.
            logger.warning(
                "MCP endpoint disabled: bind host %s reaches the network and AI_MCP_TOKEN is unset",
                agent_bind_host(),
            )
        else:
            mcp_server.register_plugin_tools(platform.plugins)
            app.mount("/mcp", mcp_guarded_app(mcp_server.asgi_app(), transport_policy))


# ── Composition ───────────────────────────────────────────────────────────────
# The object graph is built here, exactly once, in dependency order.
# Importing this module only defines it: main(), bootstrap_runtime() and the
# first access of a composed name (module __getattr__ below) call compose().

# Type declarations for the names compose() binds. Annotations only — no name
# is bound at runtime, so attribute access before composition still falls
# through to __getattr__.
logger: logging.Logger
LOG_FILE_PATH: Path
_paths: AppPaths
_registry: ModelRegistry
MODELS: list[dict[str, Any]]
FALLBACK_TEXT_MODEL: dict[str, Any]
MODEL_IDX: int
active_model_desc: str
_rag_settings: RAGSettings
EMBED_MODEL_NAME: str
USE_VISION_LLM: bool
llm_module: _LLMModule
rag_module: _RAGModule
_tool_registry: ToolRegistry
platform: PlatformContainer
agent_service: AgentService
mcp_server: McpToolServer
_agent_run_service: AgentRunService
app: FastAPI
transport_policy: TransportPolicy

_COMPOSED = False
_COMPOSE_LOCK = threading.RLock()

# The names compose() creates. Module __getattr__ composes on the first access
# of any of them, so ``server.app`` behaves exactly like an eager import while
# ``import server`` alone opens no log file, reads no config and builds no app.
_COMPOSED_NAMES = frozenset({
    "logger", "LOG_FILE_PATH", "_paths", "_registry", "MODELS",
    "FALLBACK_TEXT_MODEL", "MODEL_IDX", "active_model_desc", "_rag_settings",
    "EMBED_MODEL_NAME", "USE_VISION_LLM", "llm_module", "rag_module",
    "_tool_registry", "platform", "agent_service", "mcp_server",
    "_agent_run_service", "app", "transport_policy",
})


def _pending_actions_path() -> str:
    """Canonical on-disk location for pending approvals and UI actions.

    Where durable state lives is a composition-root decision (ADR 0003): the
    agent layer receives the file-backed store already pointed here and never
    names a path itself.
    """
    from ai_assistant.config.paths import get_paths

    paths = get_paths()
    data_root = paths.data_dir if paths.data_dir.name == "AIAssistant" else paths.data_dir / "AIAssistant"
    return str(data_root / "pending_agent_actions.json")


def compose() -> None:
    """Build the object graph exactly once, in dependency order.

    Idempotent and re-entrant (``RLock``): entry points call it
    unconditionally and internal helpers guard their first use of composed
    state with it. The flag is set before the body, so a failure re-raises on
    the first attempt and leaves the module composed-but-broken rather than
    retrying a half-built graph (a second ``setup_logging`` would duplicate
    every handler).
    """
    global _COMPOSED, logger, LOG_FILE_PATH, _paths, _registry, MODELS
    global FALLBACK_TEXT_MODEL, MODEL_IDX, active_model_desc, _rag_settings
    global EMBED_MODEL_NAME, USE_VISION_LLM, llm_module, rag_module
    global _tool_registry, platform, agent_service, mcp_server
    global _agent_run_service, app, transport_policy
    with _COMPOSE_LOCK:
        if _COMPOSED:
            return
        _COMPOSED = True

        # Heavy imports live here — not at module top — so importing this
        # module pulls no third-party framework (ADR 0022). ``settings`` is
        # the only ai_assistant module imported at top level: it owns the
        # process environment and is pure standard library.
        from ai_assistant.adapters.a2a_protocol import A2ARouter
        from ai_assistant.adapters.http import (
            build_admin_router,
            build_agent_router,
            build_chat_router,
            build_health_router,
        )
        from ai_assistant.adapters.http.security import TransportPolicy
        from ai_assistant.adapters.mcp import McpToolServer
        from ai_assistant.adapters.orchestration import LangGraphAgentOrchestrator
        from ai_assistant.adapters.persistence.checkpointing import build_checkpointer
        from ai_assistant.adapters.persistence.pending_store import PendingActionStore
        from ai_assistant.agents.service import AgentService
        from ai_assistant.application.agent_runs import AgentRunService
        from ai_assistant.bootstrap.app_factory import create_app
        from ai_assistant.bootstrap.container import build_container
        from ai_assistant.config import (
            RAGSettings,
            load_model_registry,
            resolve_paths,
            setup_logging,
        )
        from ai_assistant.llm.defaults import CHARS_PER_TOKEN, LLM_N_CTX
        from ai_assistant.tools import action_manifest
        from ai_assistant.tools.factory import create_tool_registry, platform_executors

        # Logging first: every later step reports through it.
        logger, LOG_FILE_PATH = setup_logging(str(PACKAGE_ROOT / "logs"))
        _patch_proactor_transport()

        # Model registry → model list, selector index and RAG settings.
        _paths = resolve_paths(Path(BASE_DIR))
        _registry = load_model_registry(settings_config_dir())
        MODELS = [
            {
                "repo_id": m.repo_id,
                "filename": m.filename,
                "desc": m.desc,
                **({"is_vision": m.is_vision} if m.is_vision else {}),
                **({"mmproj_repo_id": m.mmproj_repo_id} if m.mmproj_repo_id else {}),
                **({"mmproj_filename": m.mmproj_filename} if m.mmproj_filename else {}),
            }
            for m in _registry.models
        ]
        FALLBACK_TEXT_MODEL = {
            "repo_id": _registry.fallback.repo_id,
            "filename": _registry.fallback.filename,
            "desc": _registry.fallback.desc,
        }
        MODEL_IDX = _resolve_model_idx(sys.argv, MODELS)
        active_model_desc = str(MODELS[MODEL_IDX]["desc"])
        _rag_settings = RAGSettings.from_env_and_dict({})
        EMBED_MODEL_NAME = _rag_settings.embed_model_name
        # Vision path for the local model. A deployment toggle (ADR 0001: env
        # is read through a settings accessor), not a constant — hard-coding
        # ``False`` silently disabled the vision branch while
        # ``vision_llm_enabled()`` reported the operator's choice.
        USE_VISION_LLM = vision_llm_enabled()

        # Compatibility wrappers as single instances (module state owner).
        llm_module = _LLMModule()
        rag_module = _RAGModule()

        # The composition root owns the single tool catalog, coordinator and
        # agent service. The shared tool gateway, LangGraph checkpointer and
        # A2A transport are injected explicitly, so no module-level service
        # locator remains.
        _tool_registry = create_tool_registry(rag_context=rag_module)
        platform = build_container(
            Path(BASE_DIR),
            list(_tool_registry.get_all()),
            platform_executors(_tool_registry),
            _execute_a2a_task,
        )
        agent_service = AgentService(
            tool_gateway=platform.gateway,
            llm_runtime=llm_module,
            tool_registry=_tool_registry,
            checkpointer=build_checkpointer(),
            a2a_router=A2ARouter(),
            task_coordinator=platform.coordinator,
            pending_actions=PendingActionStore(_pending_actions_path()),
        )
        # The MCP adapter owns one server instance bound to the shared gateway;
        # ``mcp_server`` is what the admin routes import (the old
        # ``legacy.mcp_server`` re-export shim was deleted with legacy).
        mcp_server = McpToolServer(platform.gateway)
        _agent_run_service = AgentRunService(
            capability_scopes=platform.settings.capability_scopes,
            orchestrator=LangGraphAgentOrchestrator(
                execute=agent_service.execute,
                approve=agent_service.approve,
                ui_action_result=agent_service.ui_action_result,
            ),
        )

        # FastAPI application: transport trust policy (ADR 0008) plus every
        # HTTP surface, mounted once, sharing one parsed policy object so
        # loopback/origin/token enforcement cannot drift between adapters.
        app = create_app(platform.settings, lifespan)
        transport_policy = TransportPolicy.from_env(
            allowed_origins=platform.settings.allowed_origins,
            allow_remote_a2a=platform.settings.allow_remote_a2a,
        )
        app.include_router(build_health_router(
            llm_module=llm_module,
            rag_module=rag_module,
            platform=platform,
            server_start_time=_SERVER_START_TIME,
            embed_model_name=EMBED_MODEL_NAME,
            chars_per_token=CHARS_PER_TOKEN,
            model_idx=MODEL_IDX,
            app=app,
        ))
        app.include_router(build_chat_router(
            llm_module=llm_module,
            rag_module=rag_module,
            get_chatbot_agent=_get_chatbot_agent,
            llm_n_ctx=LLM_N_CTX,
            logger=logger,
            policy=transport_policy,
        ))
        app.include_router(build_admin_router(
            llm_module=llm_module,
            rag_module=rag_module,
            action_manifest_module=action_manifest,
            reset_agent_state_fn=agent_service.reset_state,
            models=MODELS,
            model_idx=MODEL_IDX,
            refresh_agent_routes_fn=_refresh_agent_routes,
            rebuild_chatbot_agent_fn=_rebuild_chatbot_agent,
            logger=logger,
            policy=transport_policy,
        ))
        app.include_router(build_agent_router(agent_service, policy=transport_policy))
        _register_optional_protocols(app, platform, mcp_server, transport_policy)


def __getattr__(name: str) -> Any:
    """Compose on first access of a composed name (PEP 562).

    Only fires for names missing from the module dict: a composed name before
    composition (build the graph, then return it), or a genuinely unknown
    attribute (``AttributeError``, as if there were no hook at all).
    """
    if name in _COMPOSED_NAMES:
        compose()
        return globals()[name]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


# ── Runtime Bootstrap ─────────────────────────────────────────────────────────
def bootstrap_runtime() -> None:
    """Initialize RAG and LLM once before Uvicorn accepts requests."""
    compose()
    from ai_assistant.config import safe_relpath

    logger.info(
        "Starting runtime | model index=%d | log=%s",
        MODEL_IDX, safe_relpath(LOG_FILE_PATH, BASE_DIR),
    )
    rag_module.initialize_rag(
        enable_reranker=not MODELS[MODEL_IDX].get("is_vision", False),
    )
    llm_module.load_model()
    if llm_module.is_vision_model:
        rag_module.release_embedding_for_vision()


def main() -> None:
    """Process entry point: warm the runtime, then serve until terminated.

    Extracted from the ``__main__`` guard so the two failure paths are unit
    tests rather than coverage exclusions: a runtime that will not load exits
    1 (the Qt host reads the log), and a serve that dies is re-raised after
    being logged.
    """
    import uvicorn

    try:
        compose()
        bootstrap_runtime()
    except Exception:
        logger.exception("AI server startup failed")
        sys.exit(1)
    try:
        uvicorn.run(app, host=agent_bind_host(), port=agent_bind_port(),
                    log_config=None, access_log=False)
    except BaseException:
        logger.exception("Uvicorn terminated during server startup")
        raise


if __name__ == "__main__":  # one-line interpreter guard (coverage exclude_also)
    main()
