"""Tests for the LangGraph engine wrapper (internal/langgraph_engine.py).

These tests cover the thin wrapper surface: ``compile``, ``invoke`` and
``get_state``.  The wrapper is the only module that imports ``langgraph``;
every other module reaches the engine through it.
"""
from __future__ import annotations

import unittest
from unittest import mock

from ai_assistant.internal.langgraph_engine import (
    END,
    START,
    CompiledGraph,
    LangGraphEngine,
)
from ai_assistant.orchestration.state import AgentState


class LangGraphEngineWrapperTests(unittest.TestCase):
    """The LangGraphEngine wrapper compiles and invokes graphs."""

    def test_compile_returns_compiled_graph(self) -> None:
        """``compile`` returns a CompiledGraph instance.

        The engine is built exactly the way production builds it: the caller
        hands the state schema in (the wrapper imports no orchestration
        module, so ``orchestration ↔ internal`` stays one-directional).
        """
        engine = LangGraphEngine(AgentState)
        engine.add_node("start", lambda state: state)
        engine.add_conditional_edges(START, lambda state: "start", {"start": "start"})
        engine.add_conditional_edges("start", lambda state: END, {END: END})
        compiled = engine.compile()
        self.assertIsInstance(compiled, CompiledGraph)

    def test_add_node_and_conditional_edges(self) -> None:
        """``add_node`` and ``add_conditional_edges`` are forwarded to the builder."""
        engine = LangGraphEngine(AgentState)
        engine.add_node("plan", lambda state: state)
        engine.add_conditional_edges(START, lambda state: "plan", {"plan": "plan"})
        engine.add_conditional_edges("plan", lambda state: END, {END: END})
        compiled = engine.compile()
        self.assertIsNotNone(compiled)

    def test_invoke_delegates_to_compiled_graph(self) -> None:
        """``invoke`` delegates to the compiled graph's invoke method."""
        mock_graph = mock.Mock()
        mock_graph.invoke.return_value = {"messages": [], "done": True}
        compiled = CompiledGraph(mock_graph)
        result = compiled.invoke(
            {"messages": [], "done": False},
            {"configurable": {"thread_id": "test"}},
        )
        self.assertEqual(result, {"messages": [], "done": True})
        mock_graph.invoke.assert_called_once_with(
            {"messages": [], "done": False},
            config={"configurable": {"thread_id": "test"}},
        )

    def test_get_state_delegates_to_compiled_graph(self) -> None:
        """``get_state`` delegates to the compiled graph's get_state method."""
        mock_graph = mock.Mock()
        mock_graph.get_state.return_value = {"values": {"messages": []}}
        compiled = CompiledGraph(mock_graph)
        result = compiled.get_state({"configurable": {"thread_id": "test"}})
        self.assertEqual(result, {"values": {"messages": []}})
        mock_graph.get_state.assert_called_once_with(
            {"configurable": {"thread_id": "test"}}
        )


if __name__ == "__main__":
    unittest.main()
