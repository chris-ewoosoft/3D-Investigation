"""Guard-path coverage for the orchestration graph wrapper.

``LocalAgentGraph`` has exactly two responsibilities that the node-level tests
never touch: streaming newly produced steps to the UI emitter, and restoring
``required_ui_actions`` from the LangGraph checkpoint when a run resumes. The
checkpoint read is explicitly best-effort — a corrupt or missing checkpoint
must degrade to "no pending UI actions" instead of killing the resumed run —
so both the success arm and the ``except`` arm are worth pinning.
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from ai_assistant.orchestration import graph as graph_module
from ai_assistant.orchestration.graph import (
    LocalAgentGraph,
    _bound_trace_value,
    _build_node_trace_inputs,
    _build_node_trace_outputs,
)


def _graph(emit=None, checkpointer: Any | None = None) -> LocalAgentGraph:
    if checkpointer is not None:
        # LangGraph refuses an arbitrary object; a real in-memory saver keeps
        # the compile() call honest while still exercising the resume path.
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
    return LocalAgentGraph(
        complete=lambda _messages, _temperature: "{}",
        parse=lambda _text: (None, None),
        execute=lambda _tool, _params: {},
        needs_approval=lambda _tool, _params: False,
        max_iterations=3,
        emit=emit,
        checkpointer=checkpointer,
    )


class _FakeCompiledGraph:
    """Stands in for ``builder.compile(...)`` so ``run`` can be driven alone."""

    def __init__(self, prior: Any = None, error: Exception | None = None) -> None:
        self._prior = prior
        self._error = error
        self.prior_reads = 0
        self.invoked_state: dict[str, Any] | None = None

    def get_state(self, _config) -> Any:
        self.prior_reads += 1
        if self._error is not None:
            raise self._error
        return self._prior

    def invoke(self, input_state, config=None) -> dict[str, Any]:
        self.invoked_state = dict(input_state)
        return {"done": True, "steps": []}


class StepStreamingTests(unittest.TestCase):
    """Each node may only tell the UI about steps it has not sent yet."""

    def test_steps_are_streamed_once_and_only_once(self):
        seen: list[dict[str, Any]] = []
        graph = _graph(emit=seen.append)
        pool = [
            {"type": "plan", "content": "first"},
            {"type": "plan", "content": "second"},
            {"type": "plan", "content": "third"},
        ]
        node = graph._traced("plan", lambda _state: {"steps": pool[:2], "done": False})
        state = {"iteration": 0, "routing_plan": "supervisor", "steps": []}

        node(dict(state))
        self.assertEqual([s["content"] for s in seen], ["first", "second"])

        # A later turn re-sends the full list; only the delta is new.
        node(dict(state, steps=pool[:2]))
        self.assertEqual(len(seen), 2)

        node(dict(state, steps=pool[:2]))
        self.assertEqual(len(seen), 2)

    def test_a_node_returning_no_steps_is_silent(self):
        seen: list[dict[str, Any]] = []
        graph = _graph(emit=seen.append)
        node = graph._traced("reason", lambda _state: {"done": True})
        node({"iteration": 1, "routing_plan": "supervisor", "steps": []})
        self.assertEqual(seen, [])


class NodeTracePayloadTests(unittest.TestCase):
    """Each graph node exposes the data that explains its decision."""

    @staticmethod
    def _inputs(name: str, state: dict[str, Any]) -> dict[str, Any]:
        plan = list(state.get("plan") or [])
        current_index = 1 if len(plan) > 1 else (0 if plan else None)
        current_step = plan[current_index] if current_index is not None else None
        last_call = next(
            (step for step in reversed(state.get("steps", [])) if step.get("type") == "tool_call"),
            None,
        )
        return _build_node_trace_inputs(
            name=name,
            state=state,
            plan=plan,
            current_step=current_step,
            current_step_index=current_index,
            done_count=current_index or 0,
            tool_name=last_call.get("tool", "") if last_call else "",
            tool_params=last_call.get("params", {}) if last_call else {},
        )

    def test_five_node_inputs_expose_their_decision_context_and_redact_secrets(self):
        steps = [
            {"type": "tool_call", "tool": "read_file", "params": {"path": "a.cpp"}},
            {"type": "tool_result", "tool": "read_file", "result": {"content": "ok"}},
            {"type": "verification", "tool": "read_file", "result": {"passed": True}},
            {"type": "reflection", "tool": "read_file", "result": {"passed": True}},
            {"type": "tool_call", "tool": "web_search", "params": {"api_key": "top-secret"}},
        ]
        state = {
            "messages": [
                {"role": "user", "content": "Inspect this project"},
                {"role": "system", "content": "Use the verified evidence"},
            ],
            "steps": steps,
            "iteration": 2,
            "routing_plan": "code",
            "done": False,
            "plan": ["inspect", "verify"],
            "plan_spec": {"steps": ["inspect", "verify"]},
            "plan_attempts": 2,
            "plan_feedback": "split the steps",
            "prior_plan": ["old step"],
        }

        plan_input = self._inputs("plan", state)
        self.assertEqual(plan_input["user_request"], "Inspect this project")
        self.assertEqual(plan_input["prior_plan"], ["old step"])
        self.assertEqual(plan_input["current_step"], "verify")

        review_input = self._inputs("plan_reflect", state)
        self.assertEqual(review_input["plan_to_review"], ["inspect", "verify"])
        self.assertEqual(review_input["plan_attempt"], 2)
        self.assertIn("plan_spec", review_input)

        reason_input = self._inputs("reason", state)
        self.assertEqual(reason_input["last_observation"]["result"], {"content": "ok"})
        self.assertTrue(reason_input["last_reflection"]["result"]["passed"])
        self.assertEqual(reason_input["message_count"], 2)

        tool_input = self._inputs("tool", state)
        self.assertEqual([call["tool"] for call in tool_input["tool_calls"]], ["web_search"])
        self.assertEqual(tool_input["tool_params"]["api_key"], "[redacted]")

        reflect_input = self._inputs("reflect", state)
        self.assertEqual(reflect_input["tool_call"]["tool"], "web_search")
        self.assertNotIn("tool_result", reflect_input)
        self.assertNotIn("verification", reflect_input)

    def test_reflect_input_pairs_call_result_and_verification_by_tool(self):
        state = {
            "messages": [{"role": "system", "content": "no user message"}],
            "steps": [
                {"type": "tool_call", "tool": "read_file", "params": {"path": "a.cpp"}},
                {"type": "tool_result", "tool": "read_file", "result": {"content": "ok"}},
                {"type": "verification", "tool": "read_file", "result": {"passed": True}},
            ],
            "plan": None,
        }
        payload = self._inputs("reflect", state)
        self.assertEqual(payload["user_request"], "")
        self.assertEqual(payload["tool_result"]["result"], {"content": "ok"})
        self.assertTrue(payload["verification"]["result"]["passed"])
        self.assertNotIn("current_step", payload)

    def test_node_outputs_show_state_delta_and_node_specific_result(self):
        base = {
            "messages": [{"role": "user", "content": "do it"}],
            "steps": [{"type": "plan", "steps": ["inspect"]}],
        }
        plan = ["inspect", "build"]

        plan_output = _build_node_trace_outputs(
            "plan",
            {"plan": plan, "plan_spec": {"tool_hints": ["read_file"]}, "plan_verified": False},
            base,
            plan,
        )
        self.assertEqual(plan_output["plan_step_count"], 2)
        self.assertIn("plan_spec", plan_output)

        review_step = {
            "type": "plan_reflection",
            "result": {"passed": False, "decision": "revise", "reason": "too broad"},
        }
        review_output = _build_node_trace_outputs(
            "plan_reflect",
            {"steps": [*base["steps"], review_step], "plan_verified": False, "plan_feedback": "too broad"},
            base,
            plan,
        )
        self.assertEqual(review_output["decision"], "revise")
        self.assertEqual(review_output["new_step_count"], 1)

        calls = [
            {"type": "tool_call", "tool": "read_file", "params": {"path": "a"}, "plan_step_index": 0},
            {"type": "tool_call", "tool": "web_search", "params": {"query": "b"}, "plan_step_index": 1},
        ]
        reason_output = _build_node_trace_outputs(
            "reason",
            {"steps": [*base["steps"], *calls], "pending_tool": {"tool": "read_file"}},
            base,
            plan,
        )
        self.assertEqual(reason_output["decision"], "pending_tool")
        self.assertEqual(len(reason_output["tool_calls"]), 2)
        self.assertEqual(reason_output["tool_calls"][1]["step"], "build")

        results = [
            {"type": "tool_result", "tool": "read_file", "result": {"content": "ok"}, "batch_id": "b1"},
            {"type": "tool_result", "tool": "web_search", "result": {"error": "failed"}, "batch_id": "b1"},
        ]
        tool_output = _build_node_trace_outputs(
            "tool", {"steps": [*base["steps"], *results]}, base, plan,
        )
        self.assertFalse(tool_output["success"])
        self.assertEqual(len(tool_output["tool_results"]), 2)
        self.assertNotIn("tool_result", tool_output)

        reflection = {"type": "reflection", "result": {"passed": True, "decision": "continue"}}
        reflect_output = _build_node_trace_outputs(
            "reflect",
            {
                "steps": [*base["steps"], reflection],
                "messages": [*base["messages"], {"role": "system", "content": "continue"}],
            },
            base,
            plan,
        )
        self.assertTrue(reflect_output["reflection"]["passed"])
        self.assertEqual(reflect_output["new_messages"], [{"role": "system", "content": "continue"}])

    def test_reason_and_tool_output_variants_are_explicit(self):
        base = {"messages": [], "steps": []}
        step_answer = _build_node_trace_outputs(
            "reason", {"steps": [{"type": "step_answer", "content": "partial"}]}, base, [],
        )
        self.assertEqual(step_answer["decision"], "step_answer")
        final_answer = _build_node_trace_outputs(
            "reason", {"steps": [{"type": "final_answer", "content": "done"}]}, base, [],
        )
        self.assertEqual(final_answer["decision"], "final_answer")
        pending = _build_node_trace_outputs(
            "tool", {"pending_tool": {"tool": "open", "ui_ack": True}}, base, [],
        )
        self.assertEqual(pending["pending_status"], "pending_ui_action")
        pending_approval = _build_node_trace_outputs(
            "tool", {"pending_tool": {"tool": "write", "ui_ack": False}}, base, [],
        )
        self.assertEqual(pending_approval["pending_status"], "pending_approval")
        one_result = _build_node_trace_outputs(
            "tool",
            {"steps": [{"type": "tool_result", "tool": "read", "result": {"content": "ok"}}]},
            base,
            [],
        )
        self.assertEqual(one_result["tool_name"], "read")
        self.assertEqual(one_result["tool_result"], {"content": "ok"})

    def test_trace_payload_limits_long_strings_and_collections(self):
        long_text = _bound_trace_value("x" * 5_000)
        self.assertTrue(long_text.endswith("... [truncated]"))
        self.assertEqual(_bound_trace_value("short"), "short")
        bounded_dict = _bound_trace_value({str(index): index for index in range(55)})
        self.assertEqual(bounded_dict["_trace_truncated_items"], 5)
        bounded_list = _bound_trace_value(list(range(55)))
        self.assertEqual(bounded_list[-1], {"_trace_truncated_items": 5})
        self.assertEqual(_bound_trace_value(42), 42)

    def test_empty_and_unknown_node_payloads_stay_valid(self):
        empty = {"messages": [], "steps": [], "plan": None, "done": True}
        tool_input = self._inputs("tool", empty)
        self.assertEqual(tool_input["tool_calls"], [])
        self.assertNotIn("tool_name", tool_input)
        self.assertNotIn("tool_params", tool_input)
        reflect_input = self._inputs("reflect", empty)
        self.assertNotIn("tool_call", reflect_input)
        self.assertNotIn("tool_name", reflect_input)
        unknown_input = self._inputs("custom", empty)
        self.assertEqual(unknown_input["node"], "custom")

        empty_review = _build_node_trace_outputs("plan_reflect", {}, empty, [])
        self.assertNotIn("review", empty_review)
        empty_tool = _build_node_trace_outputs("tool", {}, empty, [])
        self.assertNotIn("pending_status", empty_tool)
        empty_reflect = _build_node_trace_outputs("reflect", {}, empty, [])
        self.assertNotIn("reflection", empty_reflect)
        unknown_output = _build_node_trace_outputs("custom", {}, empty, [])
        self.assertEqual(unknown_output["node"], "custom")
        self.assertTrue(unknown_output["done"])

        invalid_index = _build_node_trace_outputs(
            "reason",
            {"steps": [{"type": "tool_call", "tool": "read", "params": {}, "plan_step_index": 99}]},
            empty,
            ["only step"],
        )
        self.assertNotIn("step", invalid_index["tool_calls"][0])

    def test_traced_wrapper_writes_one_unified_trace(self):
        graph = _graph()
        captured: dict[str, Any] = {}

        @contextmanager
        def fake_span(name: str, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs
            context = {"run_id": "node-run", "outputs": {}}
            captured["context"] = context
            yield context

        state = {
            "messages": [{"role": "user", "content": "make a plan"}],
            "steps": [],
            "iteration": 0,
            "routing_plan": "supervisor",
            "plan": None,
        }
        node = graph._traced(
            "plan",
            lambda _state: {"plan": ["inspect"], "plan_verified": False, "steps": []},
        )
        with patch.object(graph_module, "span", fake_span):
            node(state)

        self.assertEqual(captured["name"], "agent.plan")
        self.assertEqual(captured["kwargs"]["trace_inputs"]["user_request"], "make a plan")
        self.assertEqual(captured["context"]["outputs"]["plan"], ["inspect"])

    def test_traced_wrapper_accepts_disabled_context_after_plan_completion(self):
        graph = _graph()

        @contextmanager
        def disabled_span(_name: str, **_kwargs):
            yield None

        state = {
            "messages": [{"role": "user", "content": "done"}],
            "steps": [
                {
                    "type": "reflection",
                    "tool": "application_action",
                    "plan_step_index": 0,
                    "result": {"passed": True},
                },
            ],
            "iteration": 1,
            "routing_plan": "",
            "plan": ["finished"],
        }
        node = graph._traced("reason", lambda _state: {"done": True})
        with patch.object(graph_module, "span", disabled_span):
            self.assertTrue(node(state)["done"])


class CheckpointRestoreTests(unittest.TestCase):
    """A resumed run re-reads the stored UI actions, but never depends on it."""

    def _run(self, graph: LocalAgentGraph, fake: _FakeCompiledGraph):
        graph._graph = fake
        return graph.run(
            [{"role": "user", "content": "resume"}],
            session_id="sess-1",
            temperature=0.2,
            resume_with_reflection=True,
        )

    def test_a_saved_checkpoint_restores_the_pending_ui_actions(self):
        fake = _FakeCompiledGraph(
            prior=SimpleNamespace(values={"required_ui_actions": [{"action": "login"}]}),
        )
        graph = _graph(checkpointer=object())
        self._run(graph, fake)
        self.assertEqual(fake.prior_reads, 1)
        self.assertEqual(fake.invoked_state["required_ui_actions"], [{"action": "login"}])

    def test_a_checkpoint_without_saved_ui_actions_yields_an_empty_list(self):
        fake = _FakeCompiledGraph(prior=SimpleNamespace(values={}))
        graph = _graph(checkpointer=object())
        self._run(graph, fake)
        self.assertEqual(fake.invoked_state["required_ui_actions"], [])

    def test_an_absent_checkpoint_yields_an_empty_list(self):
        fake = _FakeCompiledGraph(prior=None)
        graph = _graph(checkpointer=object())
        self._run(graph, fake)
        self.assertEqual(fake.invoked_state["required_ui_actions"], [])

    def test_an_unreadable_checkpoint_does_not_abort_the_resumed_run(self):
        """The resume must proceed with no pending actions, not raise."""
        fake = _FakeCompiledGraph(error=RuntimeError("checkpointer gone"))
        graph = _graph(checkpointer=object())
        with self.assertLogs("ai_assistant.orchestration.graph", level="WARNING") as logged:
            self._run(graph, fake)
        self.assertTrue(
            any("khôi phục required_ui_actions" in line for line in logged.output),
            logged.output,
        )
        self.assertEqual(fake.invoked_state["required_ui_actions"], [])

    def test_explicit_ui_actions_skip_the_checkpoint_read(self):
        fake = _FakeCompiledGraph()
        graph = _graph(checkpointer=object())
        graph._graph = fake
        graph.run(
            [{"role": "user", "content": "resume"}],
            session_id="sess-1",
            temperature=0.2,
            resume_with_reflection=True,
            required_ui_actions=[{"action": "confirm"}],
        )
        self.assertEqual(fake.prior_reads, 0)
        self.assertEqual(fake.invoked_state["required_ui_actions"], [{"action": "confirm"}])

    def test_a_stateless_run_never_touches_the_checkpointer(self):
        """``checkpointer=None`` is the stateless path used by tests."""
        fake = _FakeCompiledGraph()
        graph = _graph()
        graph._graph = fake
        graph.run(
            [{"role": "user", "content": "hi"}],
            session_id="sess-2",
            temperature=0.2,
        )
        self.assertEqual(fake.prior_reads, 0)
        self.assertEqual(fake.invoked_state["required_ui_actions"], [])


class PriorPlanThreadingTests(unittest.TestCase):
    """The procedural-memory seed must reach the state schema (ADR 0043 §7)."""

    def _run(self, **over):
        fake = _FakeCompiledGraph()
        graph = _graph()
        graph._graph = fake
        graph.run(
            [{"role": "user", "content": "hi"}],
            session_id="sess-3",
            temperature=0.2,
            **over,
        )
        return fake.invoked_state

    def test_a_remembered_plan_reaches_the_invoked_state(self):
        invoked = self._run(prior_plan=["Buoc mot", "Buoc hai"])
        self.assertEqual(invoked["prior_plan"], ["Buoc mot", "Buoc hai"])

    def test_a_run_without_a_remembered_plan_sends_none(self):
        """The channel is always filled — ``None`` means "no recipe"."""
        invoked = self._run()
        self.assertIsNone(invoked["prior_plan"])


if __name__ == "__main__":
    unittest.main()
