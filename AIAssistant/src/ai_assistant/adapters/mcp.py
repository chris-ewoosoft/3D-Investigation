"""MCP tool server for the 3D-Reconstruction desktop agent.

The composition root injects the shared tool gateway once, at construction
time (:class:`McpToolServer`), and every tool call funnels through the
instance-bound :meth:`McpToolServer._dispatch`. There is no module-level
service locator and no later ``configure_*`` rebind — the dispatch callback is
captured when the server is built. The dispatch also binds
:data:`MCP_PRINCIPAL` so the shared gateway enforces the least-privilege scope
set for an external machine caller instead of falling back to the in-process
identity, and the composition root mounts this app behind the MCP transport
guard (``ai_assistant.adapters.http.security``).
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from ..domain.security import (
    DataClassification,
    Principal,
    bind_principal,
    classification_at_least,
    principal_for,
)
from ..ports import ToolGateway
from ..tools.aliases import canonical_tool_name
from ..tools.tool_contract import RUNTIME_UNCONFIGURED_RESULT

logger = logging.getLogger("ai_assistant.adapters.mcp")

try:
    # mcp 1.x. On mcp >= 2 the back-compat module exists in the shipped stubs
    # (mcp-types) without the FastMCP symbol — it was renamed MCPServer — so
    # mypy reports attr-defined while the runtime chain below keeps working
    # on both major versions. This is the one module that keeps
    # ``warn_unused_ignores`` off (pyproject override): on a 1.x install the
    # symbol resolves and the ignore would be reported as stale, on 2.x it is
    # load-bearing — the verdict would flip with the installed version.
    from mcp.server.fastmcp import FastMCP  # type: ignore[attr-defined]
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        FastMCP = None

MCP_AVAILABLE = FastMCP is not None

# Builtin tools, in registration order. The method names mirror the exposed
# MCP tool names so ``mcp.tool()(getattr(self, name))`` maps 1:1.
_BUILTIN_TOOLS = (
    "read_file",
    "list_directory",
    "find_files",
    "search_text",
    "analyze_code",
    "get_project_status",
    "git_diff",
    "validate_file",
    "rag_search",
    "application_action",
)

# MCP is a machine-to-machine surface with no desktop session behind it, so a
# call never inherits the broad in-process identity: the shared gateway sees
# read scope plus the desktop-ack scope ``application_action`` declares. Tools
# that write or execute (``project.write`` / ``project.execute``), including
# plugin tools declaring those scopes, are refused by the same chokepoint that
# refuses them for any other least-privilege caller.
MCP_PRINCIPAL = principal_for("mcp-caller", frozenset({"project.read", "desktop.action"}))


class McpToolServer:
    """FastMCP server bound to one injected tool gateway.

    Degrades to a no-op envelope when MCP is not installed (``available`` is
    False and ``asgi_app()`` returns ``None``), so the composition root and the
    offline test suite can import this module unconditionally.
    """

    def __init__(self, gateway: ToolGateway | None = None) -> None:
        self._gateway = gateway
        self._server: Any = None
        self._asgi_app: Any = None
        if MCP_AVAILABLE:
            self._server = FastMCP(
                "3D-Reconstruction Tools",
                instructions=(
                    "Safe tools for inspecting the 3D-Reconstruction project and dispatching "
                    "a desktop action. Desktop actions are only complete after the Qt client acknowledges them."
                ),
            )
            for name in _BUILTIN_TOOLS:
                self._server.tool()(getattr(self, name))
            self._asgi_app = self._server.streamable_http_app(
                streamable_http_path="/", stateless_http=True, json_response=True,
            )

    @property
    def available(self) -> bool:
        """True when a real MCP server could be constructed."""
        return self._server is not None

    def asgi_app(self) -> Any:
        """Return the Streamable HTTP application, or None when MCP is not installed."""
        return self._asgi_app

    def _dispatch(self, tool_name: str, parameters: dict[str, Any]) -> str:
        """Adapt an MCP call to the platform's single policy-enforced tool path."""
        parameters = {name: value for name, value in parameters.items() if value is not None}
        if self._gateway is None:
            return json.dumps(RUNTIME_UNCONFIGURED_RESULT, ensure_ascii=False)
        with bind_principal(MCP_PRINCIPAL):
            return json.dumps(self._gateway.execute(tool_name, parameters), ensure_ascii=False)

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Start/stop the MCP session manager when mounted inside FastAPI."""
        if self._server is None:
            yield
            return
        async with self._server.session_manager.run():
            yield

    def register_plugin_tools(self, plugins: Any = None) -> int:
        """Dynamically bind tools from an injected PluginRegistry into MCP.

        ``plugins`` is supplied by the composition root (``platform.plugins``);
        without it this is a no-op rather than reaching for a global container.
        Builtin names are reserved so plugin specs that mirror the builtin
        catalog never get re-registered (the ``MCPServer`` fallback does not
        expose ``get_tool``).
        """
        return bind_plugin_tools(
            self._server, self._dispatch, plugins,
            reserved=set(_BUILTIN_TOOLS), principal=MCP_PRINCIPAL,
        )

    def list_registered_tools(self) -> list[str]:
        """Names of builtin tools this server exposes (for health/debugging)."""
        return list(_BUILTIN_TOOLS)

    # ── Builtin tools ─────────────────────────────────────────────────────────
    # Each is defined as a method so the same callable is both registered on
    # the server and directly testable.
    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        symbol: str | None = None,
    ) -> str:
        """Read a focused range or named source symbol within the project."""
        return self._dispatch("read_file", {
            "path": path, "start_line": start_line, "end_line": end_line, "symbol": symbol,
        })

    def list_directory(self, path: str, recursive: bool | None = None, max_depth: int | None = None) -> str:
        """List files and folders within the project directory."""
        return self._dispatch("list_directory", {"path": path, "recursive": recursive, "max_depth": max_depth})

    def find_files(self, pattern: str, path: str | None = None, max_results: int | None = None) -> str:
        """Find project files by glob without reading their contents."""
        return self._dispatch("find_files", {"pattern": pattern, "path": path, "max_results": max_results})

    def search_text(
        self,
        query: str,
        path: str | None = None,
        file_pattern: str | None = None,
        case_sensitive: bool | None = None,
        max_results: int | None = None,
    ) -> str:
        """Search project text and return matching file paths and line numbers."""
        return self._dispatch("search_text", {
            "query": query, "path": path, "file_pattern": file_pattern,
            "case_sensitive": case_sensitive, "max_results": max_results,
        })

    def analyze_code(self, path: str) -> str:
        """Summarise the imports, classes, and functions in a source file."""
        return self._dispatch("analyze_code", {"path": path})

    def get_project_status(self) -> str:
        """Get the current Git branch, modified files, and source summary."""
        return self._dispatch("get_project_status", {})

    def git_diff(self, path: str | None = None, staged: bool | None = None) -> str:
        """Read the current Git diff for Code Agent review."""
        return self._dispatch("git_diff", {"path": path, "staged": staged})

    def validate_file(self, path: str) -> str:
        """Syntax-check a Python file or parse a JSON file without changing it."""
        return self._dispatch("validate_file", {"path": path})

    def rag_search(self, query: str, top_k: int | None = None) -> str:
        """Search indexed project documentation and source code."""
        return self._dispatch("rag_search", {"query": query, "top_k": top_k})

    def application_action(
        self,
        action: str,
        language: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> str:
        """Request a canonical desktop action; the Qt desktop client must acknowledge it."""
        return self._dispatch("application_action", {
            "action": action, "language": language,
            "username": username, "password": password,
        })


def _visible_to_principal(spec: Any, principal: Principal | None) -> bool:
    """Whether a catalogue spec belongs on this principal's MCP surface.

    MCP has no HITL approval flow, so an approval-gated tool must not be
    advertised at all: advertising it and refusing every call is a broken
    contract, not a safe default.  A tool whose scope exceeds the principal's
    grant, or whose data ceiling exceeds its clearance, is filtered the same
    way.  Aliases are never separate MCP tools.
    """
    if canonical_tool_name(str(spec.name)) != spec.name:
        return False
    if getattr(spec, "requires_approval", False):
        return False
    if principal is None:
        return True
    if not principal.permits(getattr(spec, "required_scope", None)):
        return False
    ceiling = getattr(spec, "maximum_classification", None)
    return not (
        isinstance(ceiling, DataClassification)
        and not classification_at_least(principal.classification, ceiling)
    )


def bind_plugin_tools(
    server: Any,
    dispatch: Callable[[str, dict[str, Any]], str],
    plugins: Any = None,
    *,
    reserved: set[str] | None = None,
    principal: Principal | None = None,
) -> int:
    """Bind dynamic tools from a PluginRegistry through the given dispatch.

    ``dispatch`` is the instance-bound gateway dispatcher of the owning server;
    the composition root never reaches into this module for a global gateway.
    ``reserved`` holds names that must never be shadowed (the builtin catalog).
    ``principal`` filters the binding to what that caller can actually execute
    (see :func:`_visible_to_principal`); passing it is the MCP server's
    contract, while direct unit callers may omit it.
    """
    if server is None or plugins is None:
        return 0
    reserved = reserved or set()
    registered = 0
    try:
        for spec in plugins.specs():
            tool_name = spec.name
            if tool_name in reserved:
                continue
            if not _visible_to_principal(spec, principal):
                continue
            if hasattr(server, "get_tool") and server.get_tool(tool_name):
                continue

            def _make_tool(name: str, desc: str) -> Callable[..., str]:
                def _tool(**kwargs: Any) -> str:
                    return dispatch(name, kwargs)

                _tool.__name__ = name
                _tool.__doc__ = desc
                return _tool

            if hasattr(server, "tool"):
                server.tool()(_make_tool(tool_name, spec.description or f"Dynamic tool {tool_name}"))
                registered += 1
    except Exception:
        # A bad plugin spec must not silently disable the rest of the MCP
        # surface: the loop ends, the tools registered before it still count,
        # and the failure goes on the record instead of vanishing.
        logger.warning("MCP plugin tool binding aborted", exc_info=True)
    return registered


__all__ = [
    "MCP_AVAILABLE",
    "MCP_PRINCIPAL",
    "McpToolServer",
    "bind_plugin_tools",
]
