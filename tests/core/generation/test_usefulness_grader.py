import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json
from pathlib import Path

# 将项目根目录添加到 sys.path
project_root = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
sys.path.insert(0, str(project_root))

from core.workflow.video_summary.state import VideoSummaryState
from core.workflow.video_summary.nodes.usefulness_grader import usefulness_grader_node, MAX_REVISIONS

class TestUsefulnessGraderNode(unittest.TestCase):
    
    def setUp(self):
        """准备符合最新架构 State 的测试数据"""
        self.valid_state: VideoSummaryState = {
            "transcript": "",
            "keyframes": [],
            "user_prompt": "侧重芯片性能的描述",
            "draft_summary": "这是一款很棒的手机，性能很强。",
            "aggregated_chunk_insights": "音频：发布会介绍了骁龙8 Gen3芯片，跑分达到200万。视觉：PPT 展示了芯片架构图。",
            "revision_count": 0,
            "hallucination_score": "no",  # 前置条件：已通过幻觉审查
            "usefulness_score": "",
            "feedback_instructions": ""
        }

    def test_short_circuit_empty_draft_or_prompt(self):
        """边界情况 1：草稿为空，或用户未提供特定需求时，直接短路放行 (yes)"""
        # 场景 A: 草稿为空
        state_empty_draft = self.valid_state.copy()
        state_empty_draft["draft_summary"] = ""
        result1 = usefulness_grader_node(state_empty_draft)
        self.assertEqual(result1["usefulness_score"], "yes")

        # 场景 B: 用户输入为空 (默认全面总结，无需单独打分有用性)
        state_empty_prompt = self.valid_state.copy()
        state_empty_prompt["user_prompt"] = "   "
        result2 = usefulness_grader_node(state_empty_prompt)
        self.assertEqual(result2["usefulness_score"], "yes")

    def test_short_circuit_max_revisions(self):
        """边界情况 2：达到最大重写次数，短路放行（防死循环）"""
        state = self.valid_state.copy()
        state["revision_count"] = MAX_REVISIONS
        result = usefulness_grader_node(state)
        self.assertEqual(result["usefulness_score"], "yes")

    @patch.dict(os.environ, clear=True)
    def test_missing_api_key(self):
        """边界情况 3：缺少 API Key 报错"""
        with self.assertRaisesRegex(ValueError, ".*OPENAI_API_KEY.*"):
            usefulness_grader_node(self.valid_state)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123", "OPENAI_BASE_URL": "https://fake.url"})
    @patch('core.workflow.video_summary.nodes.usefulness_grader.get_model_for_capability')
    def test_grader_yes_useful(self, mock_get_model):
        """一般情况 1：内容满足需求，返回 score='yes'"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        mock_model.chat_completion.return_value = json.dumps({
            "score": "yes",
            "reason": ""
        })
        
        result = usefulness_grader_node(self.valid_state)
        
        mock_model.chat_completion.assert_called_once()
        
        # 验证 JSON Mode
        call_kwargs = mock_model.chat_completion.call_args.kwargs
        self.assertEqual(call_kwargs.get("response_format"), {"type": "json_object"})
        
        self.assertEqual(result["usefulness_score"], "yes")
        self.assertEqual(result["feedback_instructions"], "")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123", "OPENAI_BASE_URL": "https://fake.url"})
    @patch('core.workflow.video_summary.nodes.usefulness_grader.get_model_for_capability')
    def test_grader_no_useful(self, mock_get_model):
        """一般情况 2：检测到偏题，返回 score='no' 及反馈指令"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        mock_model.chat_completion.return_value = json.dumps({
            "score": "no",
            "reason": "完全没有提到芯片性能，请大幅补充。"
        })
        
        result = usefulness_grader_node(self.valid_state)
        
        self.assertEqual(result["usefulness_score"], "no")
        self.assertIn("需求未满足", result["feedback_instructions"])
        self.assertIn("完全没有提到芯片性能", result["feedback_instructions"])

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123", "OPENAI_BASE_URL": "https://fake.url"})
    @patch('core.workflow.video_summary.nodes.usefulness_grader.get_model_for_capability')
    def test_human_guidance_included_in_review_requirements(self, mock_get_model):
        """第二阶段：有 human_guidance 时，评分输入必须包含该指导信息。"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        mock_model.chat_completion.return_value = json.dumps({"score": "yes", "reason": ""})

        state = self.valid_state.copy()
        state["user_prompt"] = ""
        state["human_guidance"] = "请优先检查是否响应了我对风险评估的强调。"  # type: ignore[index]

        result = usefulness_grader_node(state)
        self.assertEqual(result["usefulness_score"], "yes")

        call_kwargs = mock_model.chat_completion.call_args.kwargs
        messages = call_kwargs.get("messages", [])
        user_msg = [msg["content"] for msg in messages if msg["role"] == "user"][0]
        self.assertIn("人类审批补充指导", user_msg)
        self.assertIn("风险评估", user_msg)

    @patch('builtins.print')
    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123"})
    @patch('core.workflow.video_summary.nodes.usefulness_grader.get_model_for_capability')
    def test_grader_api_or_json_error(self, mock_get_model, mock_print):
        """边界情况 4：API报错或 JSON 解析失败，降级为满足需求 (yes) 防止卡死"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        mock_model.chat_completion.return_value = "这是一段普通的文字，不是 JSON"
        
        result = usefulness_grader_node(self.valid_state)
        self.assertEqual(result["usefulness_score"], "yes", "非 JSON 响应必须降级放行")
        
        print_args = [call_args[0][0] for call_args in mock_print.call_args_list]
        json_error_logged = any("Error or Invalid JSON" in arg for arg in print_args)
        self.assertTrue(json_error_logged, "系统应当详细记录 JSON 解析失败的降级日志")
        
        mock_print.reset_mock()
        
        # 模拟网络异常
        mock_model.chat_completion.side_effect = Exception("API Server Timeout")
        result2 = usefulness_grader_node(self.valid_state)
        self.assertEqual(result2["usefulness_score"], "yes", "网络异常必须降级放行")

        print_args_timeout = [call_args[0][0] for call_args in mock_print.call_args_list]
        timeout_error_logged = any("API Server Timeout" in arg for arg in print_args_timeout)
        self.assertTrue(timeout_error_logged, "系统应当详细记录 API 超时导致降级的日志")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123"})
    @patch('core.workflow.video_summary.nodes.usefulness_grader.get_model_for_capability')
    def test_grader_injects_evidence_boundary_into_llm_call(self, mock_get_model):
        """新增：aggregated_chunk_insights 应当被注入为证据边界，出现在 LLM user_content 中"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        mock_model.chat_completion.return_value = json.dumps({"score": "yes", "reason": ""})

        usefulness_grader_node(self.valid_state)

        call_kwargs = mock_model.chat_completion.call_args.kwargs
        messages = call_kwargs.get("messages", [])
        user_content = next(
            (m["content"] for m in messages if m.get("role") == "user"), ""
        )
        self.assertIn("骁龙8 Gen3", user_content)
        self.assertIn("Evidence Boundary", user_content)

        # system prompt 应包含防幻觉放大约束
        system_content = next(
            (m["content"] for m in messages if m.get("role") == "system"), ""
        )
        self.assertIn("防止幻觉放大", system_content)
        self.assertIn("证据边界", system_content)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123"})
    @patch('core.workflow.video_summary.nodes.usefulness_grader.get_model_for_capability')
    def test_grader_gracefully_handles_empty_insights(self, mock_get_model):
        """新增：aggregated_chunk_insights 为空时不崩溃，证据边界块不出现在 user_content 中"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        mock_model.chat_completion.return_value = json.dumps({"score": "yes", "reason": ""})

        for empty_val in ("", None, "   "):
            state = {**self.valid_state, "aggregated_chunk_insights": empty_val}
            result = usefulness_grader_node(state)
            self.assertEqual(result["usefulness_score"], "yes")

            call_kwargs = mock_model.chat_completion.call_args.kwargs
            messages = call_kwargs.get("messages", [])
            user_content = next(
                (m["content"] for m in messages if m.get("role") == "user"), ""
            )
            self.assertNotIn("Evidence Boundary", user_content)


if __name__ == '__main__':
    unittest.main()