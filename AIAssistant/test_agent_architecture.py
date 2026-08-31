import unittest

from LangGraphAgent import LocalAgentGraph
from modules.task_coordinator import TaskCoordinator


class AgentArchitectureTests(unittest.TestCase):
    def test_coordinator_cancellation_is_session_scoped(self):
        coordinator = TaskCoordinator()
        coordinator.start("session-1", task="generic")
        snapshot = coordinator.cancel("session-1")
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertTrue(coordinator.is_cancelled("session-1"))
        self.assertFalse(coordinator.is_cancelled("session-2"))

    def test_graph_stops_cooperatively_before_model_call(self):
        calls = []

        def complete(*_args):
            calls.append(True)
            return '{"kind":"final","content":"done"}'

        graph = LocalAgentGraph(
            complete=complete,
            parse=lambda _text: (None, None),
            execute=lambda _tool, _params: {},
            needs_approval=lambda _tool: False,
            max_iterations=3,
            cancel_checker=lambda: True,
        )
        state = graph.run([{"role": "user", "content": "task"}], "cancel-test", 0.2)
        self.assertTrue(state["cancelled"])
        self.assertTrue(any(step["type"] == "cancelled" for step in state["steps"]))
        # Planning may already be in flight when cancellation arrives; the
        # cooperative boundary guarantees no reason/tool call is dispatched.
        self.assertLessEqual(len(calls), 1)

    def test_structured_planner_payload_is_normalised(self):
        def planner(_messages, _temperature):
            return (
                '{"requires_plan":true,"goal":"fix","affected_areas":["src"],'
                '"acceptance_criteria":["tests pass"],"verification_commands":["ctest"],'
                '"steps":["inspect","change"]}'
            )
        graph = LocalAgentGraph(
            complete=lambda _messages, _temperature: '{"kind":"final","content":"done"}',
            parse=lambda _text: (None, None),
            execute=lambda _tool, _params: {},
            needs_approval=lambda _tool: False,
            max_iterations=2,
            plan_complete=planner,
        )
        state = graph.run([{"role": "user", "content": "task"}], "plan-test", 0.2)
        self.assertEqual(state["plan"], ["inspect", "change"])
        self.assertEqual(state["plan_spec"]["acceptance_criteria"], ["tests pass"])

    def test_mutation_returns_plan_scoped_approval_preview(self):
        graph = LocalAgentGraph(
            complete=lambda _messages, _temperature: "mutation",
            parse=lambda _text: ("patch_file", {"path": "src/x.cpp", "patch": "..."}),
            execute=lambda _tool, _params: {},
            needs_approval=lambda tool: tool == "patch_file",
            max_iterations=1,
        )
        state = graph.run([{"role": "user", "content": "change source"}], "approval-test", 0.2)
        preview = state["pending_tool"]["approval_preview"]
        self.assertTrue(preview["scope_id"])
        self.assertEqual(preview["tool"], "patch_file")


if __name__ == "__main__":
    unittest.main()
