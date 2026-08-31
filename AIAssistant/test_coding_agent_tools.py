"""Regression tests for the Code Agent toolbox and shared tool contract."""
from __future__ import annotations

import unittest

from modules.agent_module import (
    AGENT_TOOLS,
    _agent_safe_path,
    tool_find_files,
    tool_git_diff,
    tool_search_text,
)
from modules.coding_agent import coding_workflow_kind, coding_workflow_status
from modules.multi_agent import CODE_AGENT_TOOLS


class CodingAgentToolTests(unittest.TestCase):
    def test_every_coding_tool_has_schema_and_operational_policy(self) -> None:
        definitions = {tool["name"]: tool for tool in AGENT_TOOLS}
        self.assertTrue(CODE_AGENT_TOOLS <= definitions.keys())
        for name in CODE_AGENT_TOOLS:
            tool = definitions[name]
            self.assertEqual(tool["schema"]["type"], "object")
            self.assertIn("additionalProperties", tool["schema"])
            self.assertGreater(tool["timeout_seconds"], 0)
            self.assertIsInstance(tool["policy"], str)
            self.assertIn("requires_approval", tool)

    def test_find_files_returns_project_relative_paths(self) -> None:
        result = tool_find_files({"pattern": "Agentic_AI_Roadmap.md"})
        self.assertFalse(result.get("error"))
        self.assertIn("AIAssistant\\Agentic_AI_Roadmap.md", result["matches"])

    def test_git_diff_is_read_only_and_bounded(self) -> None:
        result = tool_git_diff({"path": "README.md"})
        self.assertNotIn("return_code", result)
        self.assertLessEqual(len(result.get("content", "")), 16000)

    def test_search_accepts_project_relative_patterns(self) -> None:
        result = tool_search_text({
            "query": "class",
            "path": "src",
            "file_pattern": "modules/*.h;services/*.h",
            "case_sensitive": True,
            "max_results": 10,
        })
        self.assertNotIn("error", result)

    def test_path_validation_rejects_sibling_prefix_escape(self) -> None:
        self.assertIsNone(_agent_safe_path("..\\3D-Reconstruction-secrets\\token.txt"))

    def test_workflow_classification_is_domain_agnostic(self) -> None:
        self.assertEqual(coding_workflow_kind("Bổ sung chức năng export dữ liệu"), "change")
        self.assertEqual(coding_workflow_kind("Phân tích và giải thích chức năng của hàm"), "analysis")

    def test_change_workflow_requires_observable_stages_in_order(self) -> None:
        task = "Implement a feature in the project"
        self.assertEqual(coding_workflow_status(task, []).missing,
                         ("source_evidence", "approved_change"))

        steps = [
            {"type": "tool_result", "tool": "read_file", "result": {"content": "source"}},
            {"type": "tool_result", "tool": "patch_file", "result": {"success": True}},
        ]
        self.assertEqual(coding_workflow_status(task, steps).missing,
                         ("diff_review", "verification_command"))

        steps.extend([
            {"type": "tool_result", "tool": "git_diff", "result": {"content": "diff"}},
            {"type": "tool_result", "tool": "run_command", "result": {"return_code": 0}},
        ])
        status = coding_workflow_status(task, steps)
        self.assertFalse(status.missing)
        self.assertTrue(status.mutation)
        self.assertTrue(status.diff_reviewed)
        self.assertTrue(status.execution_observed)

    def test_failed_command_cannot_complete_verification_stage(self) -> None:
        steps = [
            {"type": "tool_result", "tool": "read_file", "result": {"content": "source"}},
            {"type": "tool_result", "tool": "patch_file", "result": {"success": True}},
            {"type": "tool_result", "tool": "git_diff", "result": {"content": "diff"}},
            {"type": "tool_result", "tool": "run_command", "result": {"return_code": 1}},
        ]
        status = coding_workflow_status("Implement a feature in the project", steps)
        self.assertIn("verification_command", status.missing)


if __name__ == "__main__":
    unittest.main()
