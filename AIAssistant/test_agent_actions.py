import os
import sys
import unittest

# Add AIAssistant to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from modules.agent_module import (
    _build_code_citation_result,
    _match_desktop_action,
    _match_desktop_action_sequence,
    _requested_code_symbol,
    tool_analyze_code,
    tool_read_file,
    tool_search_text,
)


class TestAgentActions(unittest.TestCase):
    def test_admin_login(self):
        res = _match_desktop_action("dang nhap he thong")
        self.assertEqual(res, {"action": "admin.login", "username": "Admin", "password": "1"})
        
        res2 = _match_desktop_action("please login")
        self.assertEqual(res2, {"action": "admin.login", "username": "Admin", "password": "1"})

    def test_admin_logout(self):
        self.assertEqual(_match_desktop_action("dang xuat"), {"action": "admin.logout"})
        self.assertEqual(_match_desktop_action("logout now"), {"action": "admin.logout"})

    def test_mail_open(self):
        self.assertEqual(_match_desktop_action("mo hop thu cua toi"), {"action": "mail.open"})
        self.assertEqual(_match_desktop_action("open mail"), {"action": "mail.open"})
        self.assertEqual(_match_desktop_action("mo mail"), {"action": "mail.open"})

    def test_assistant_reloads(self):
        self.assertEqual(_match_desktop_action("tai lai model"), {"action": "assistant.reload_model"})
        self.assertEqual(_match_desktop_action("reload rag"), {"action": "assistant.reload_rag"})
        self.assertEqual(_match_desktop_action("tai lai agent"), {"action": "assistant.reload_agent"})
        self.assertEqual(_match_desktop_action("reload server"), {"action": "assistant.reload_server"})
        
    def test_language_change(self):
        self.assertEqual(_match_desktop_action("doi sang english"), {"action": "language.change", "language": "en"})
        self.assertEqual(_match_desktop_action("doi sang tieng viet"), {"action": "language.change", "language": "vi"})

    def test_admin_settings(self):
        self.assertEqual(_match_desktop_action("mo cai dat"), {"action": "admin.settings"})
        self.assertEqual(_match_desktop_action("settings"), {"action": "admin.settings"})

    def test_avatar_and_password(self):
        self.assertEqual(_match_desktop_action("doi avatar"), {"action": "admin.change_avatar"})
        self.assertEqual(_match_desktop_action("change password"), {"action": "admin.change_password"})

    def test_ai_actions(self):
        self.assertEqual(_match_desktop_action("run detection"), {"action": "ai.run_detection"})
        self.assertEqual(_match_desktop_action("phan doan"), {"action": "ai.run_segmentation"})
        self.assertEqual(_match_desktop_action("theo doi video"), {"action": "ai.video_tracking"})
        self.assertEqual(_match_desktop_action("an ket qua"), {"action": "ai.hide_results"})
        self.assertEqual(_match_desktop_action("train model"), {"action": "ai.training_model"})
        self.assertEqual(_match_desktop_action("bieu do training"), {"action": "ai.view_training_charts"})

    def test_reconstruction_actions(self):
        self.assertEqual(_match_desktop_action("bat dau tai tao"), {"action": "reconstruction.start_reconstruction"})
        self.assertEqual(_match_desktop_action("xem 3d model"), {"action": "reconstruction.view_3d_model"})
        self.assertEqual(_match_desktop_action("close 3d model"), {"action": "reconstruction.close_3d_model"})
        self.assertEqual(_match_desktop_action("tai anh tai tao"), {"action": "reconstruction.load_images"})

    def test_reconstruction_load_then_start_workflow(self):
        self.assertEqual(
            _match_desktop_action_sequence(
                "\u0054\u1ea3i \u1ea3nh ch\u1ee5p v\u00e0 t\u00e1i t\u1ea1o 3d."
            ),
            [
                {"action": "reconstruction.load_images"},
                {"action": "reconstruction.start_reconstruction"},
            ],
        )

    def test_viewer_actions(self):
        self.assertEqual(_match_desktop_action("tai 2d"), {"action": "viewer.load_2d"})
        self.assertEqual(_match_desktop_action("tai 3d"), {"action": "viewer.load_3d"})
        self.assertEqual(_match_desktop_action("Tải giúp tôi mô hình 3d."), {"action": "viewer.load_3d"})
        self.assertEqual(_match_desktop_action("load 3d model"), {"action": "viewer.load_3d"})
        self.assertEqual(_match_desktop_action("load dicom"), {"action": "viewer.load_dicom"})

    def test_about(self):
        self.assertEqual(_match_desktop_action("gioi thieu"), {"action": "help.about"})
        self.assertEqual(_match_desktop_action("about"), {"action": "help.about"})

    def test_read_file_requires_scope_for_large_cpp_and_supports_symbol(self):
        path = "src/modules/ReconstructionPlugin/ReconstructionPlugin.cpp"
        unscoped = tool_read_file({"path": path})
        self.assertIn("cần đọc theo phạm vi", unscoped["error"])
        focused = tool_read_file({
            "path": path,
            "symbol": "ReconstructionPlugin::onRunReconstruction",
        })
        self.assertEqual(focused["showing"], "lines 225-291")
        self.assertIn("void ReconstructionPlugin::onRunReconstruction", focused["content"])
        bounded = tool_read_file({"path": path, "start_line": 225, "end_line": 273})
        self.assertEqual(bounded["showing"], "lines 225-291")

    def test_qualified_code_citation_reads_complete_function_without_llm(self):
        result = _build_code_citation_result(
            "Trích dẫn hàm ReconstructionPlugin::onRunReconstruction()",
            "citation-test", 0.0,
        )
        self.assertIsNotNone(result)
        answer = result["steps"][-1]["content"]
        self.assertIn("```cpp", answer)
        self.assertIn("lines 225-291", answer)
        self.assertTrue(answer.rstrip().endswith("```"))
        read_result = next(step["result"] for step in result["steps"]
                           if step.get("type") == "tool_result" and step.get("tool") == "read_file")
        self.assertEqual(read_result["presentation"], "citation_source")

    def test_python_symbol_is_extracted_from_def_citation_request(self):
        self.assertEqual(
            _requested_code_symbol(
                "Cite function def _build_code_citation_result in AIAssistant/modules/agent_module.py"
            ),
            "_build_code_citation_result",
        )

    def test_analysis_request_is_not_reduced_to_a_source_only_citation(self):
        result = _build_code_citation_result(
            "Analyze and explain function _build_code_citation_result in AIAssistant/modules/agent_module.py",
            "analysis-test", 0.0,
        )
        self.assertIsNone(result)

    def test_python_code_citation_reads_exact_function_without_llm(self):
        result = _build_code_citation_result(
            "Cite function def _build_code_citation_result in AIAssistant/modules/agent_module.py",
            "python-citation-test", 0.0,
        )
        self.assertIsNotNone(result)
        answer = result["steps"][-1]["content"]
        self.assertIn("```python", answer)
        self.assertIn("def _build_code_citation_result", answer)
        self.assertTrue(answer.rstrip().endswith("```"))
        read_result = next(step["result"] for step in result["steps"]
                           if step.get("type") == "tool_result" and step.get("tool") == "read_file")
        self.assertEqual(read_result["path"].replace("\\", "/"), "AIAssistant/modules/agent_module.py")
        self.assertEqual(read_result["presentation"], "citation_source")

    def test_search_text_accepts_multiple_file_patterns(self):
        result = tool_search_text({
            "query": "def _build_code_citation_result",
            "path": "AIAssistant",
            "file_pattern": "*.cpp;*.py",
            "max_results": 10,
        })
        self.assertGreater(result["count"], 0)
        self.assertTrue(any(item["file"].endswith("agent_module.py") for item in result["results"]))

    def test_search_text_accepts_a_file_path_and_extension_shorthand(self):
        result = tool_search_text({
            "query": "def _build_code_citation_result",
            "path": "AIAssistant/modules/agent_module.py",
            "file_pattern": ".py",
            "max_results": 10,
        })
        self.assertGreater(result["count"], 0)
        self.assertTrue(all(item["file"].endswith("agent_module.py") for item in result["results"]))

    def test_python_analysis_keeps_top_level_functions_when_classes_exist(self):
        result = tool_analyze_code({"path": "AIAssistant/modules/agent_module.py"})
        self.assertIn("_build_code_citation_result", [item["name"] for item in result["functions"]])

if __name__ == '__main__':
    unittest.main()
