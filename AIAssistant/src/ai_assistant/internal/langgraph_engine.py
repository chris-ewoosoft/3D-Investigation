"""Low-level LangGraph engine wrapper.

This is the **only** module in the codebase that imports ``langgraph``.
Every other module — including ``orchestration/graph.py`` — reaches the
engine through :class:`LangGraphEngine`, so the framework dependency is
confined to one wrapper (ADR 0003/0012).

The wrapper exposes a minimal surface: :meth:`LangGraphEngine.compile` builds
a compiled graph from nodes and conditional-edge resolvers, and
:meth:`CompiledGraph.invoke` runs it.  The higher-level ``LocalAgentGraph`` in
``orchestration/graph.py`` owns the ReAct + Plan-and-Execute topology; this
module owns the framework.

**The state schema arrives as a constructor argument, never as an import.**
The wrapper used to import ``orchestration.state.AgentState``, which tied the
infrastructure wrapper back to the layer that consumes it
(``orchestration → internal → orchestration`` — a package-level cycle the
import contracts could not see because the modules themselves were acyclic).
``orchestration/graph.py`` now passes its own schema in, so this module
imports nothing from ``ai_assistant``: the dependency between the two layers
points one way only, pinned by
``tests/integration/test_architecture_boundaries.py``.

This module lives in ``internal/`` rather than ``adapters/`` because
Clean Architecture forbids ``orchestration/`` from importing ``adapters/``
(ADR 0002). The wrappers here are infrastructure concerns, not protocol
adapters.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, TypeVar, cast

from langgraph.graph import END, START, StateGraph

#: The graph state type is supplied by the caller, so the infrastructure
#: wrapper never names the orchestration layer's model.
StateT = TypeVar("StateT")


class LangGraphEngine(Generic[StateT]):
    """Minimal wrapper around ``langgraph.graph.StateGraph``.

    The wrapper is intentionally thin: it compiles a graph from a set of
    named nodes and conditional-edge resolvers, then invokes it.  No policy,
    no domain types and no ``ai_assistant`` imports — the schema is the only
    thing this layer needs to know about the run.

    LangGraph's typed overloads reject ReAct nodes returning plain
    ``dict[str, Any]`` partials; cast through Any at this single boundary.
    Runtime behavior is covered by the agent e2e/integration tests.
    """

    def __init__(self, state_schema: type[StateT]) -> None:
        self._builder: StateGraph[Any] = StateGraph(cast(Any, state_schema))

    def add_node(self, name: str, handler: Callable[[StateT], dict[str, Any]]) -> None:
        """Register a typed node handler."""
        self._builder.add_node(name, cast(Any, handler))

    def add_conditional_edges(
        self,
        source: str,
        resolver: Callable[[StateT], str],
        path_map: dict[str, str],
    ) -> None:
        """Register a conditional-edge resolver from ``source``."""
        self._builder.add_conditional_edges(source, resolver, cast(Any, path_map))

    def compile(self, checkpointer: Any | None = None) -> CompiledGraph[StateT]:
        """Compile the graph, optionally with a checkpointer."""
        return CompiledGraph(self._builder.compile(checkpointer=checkpointer))


class CompiledGraph(Generic[StateT]):
    """A compiled LangGraph graph that can be invoked."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    def invoke(self, state: dict[str, Any], config: dict[str, Any]) -> StateT:
        """Run the graph with the given initial state and config."""
        return cast(StateT, self._graph.invoke(state, config=config))

    def get_state(self, config: dict[str, Any]) -> Any:
        """Return the current checkpointed state for a config."""
        return self._graph.get_state(config)


__all__ = ["END", "START", "CompiledGraph", "LangGraphEngine"]
