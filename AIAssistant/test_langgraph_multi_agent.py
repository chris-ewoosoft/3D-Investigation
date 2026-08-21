import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from LangGraphAgent import LocalAgentGraph


class LangGraphMultiAgentTests(unittest.TestCase):
    def test_delegation_and_verification_steps_are_emitted(self):
        replies = iter(["CALL", "DONE"])
        graph = LocalAgentGraph(
            complete=lambda _messages, _temperature: next(replies),
            parse=lambda text: ("read_file", {"path": "README.md"}) if text == "CALL" else (None, None),
            execute=lambda _tool, _params: {"success": True},
            needs_approval=lambda _tool: False,
            max_iterations=3,
            select_specialist=lambda _tool, _params: {"specialist": "research", "idempotency_key": "key"},
            verify_result=lambda _tool, _params, _result: {"passed": True, "reason": "ok"},
            reflect_result=lambda _tool, _params, _result, _verification: {
                "passed": True, "decision": "continue", "reason": "reviewed"},
        )
        state = graph.run([
            {"role": "system", "content": "test"},
            {"role": "user", "content": "short"},
        ], "multi-agent-test", 0.1)
        self.assertEqual([step["type"] for step in state["steps"]],
                         ["thinking", "delegation", "tool_call", "tool_result", "verification", "reflection", "final_answer"])


if __name__ == "__main__":
    unittest.main()
