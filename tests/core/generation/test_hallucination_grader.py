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
from core.workflow.video_summary.nodes.hallucination_grader import hallucination_grader_node, MAX_REVISIONS

class TestHallucinationGraderNode(unittest.TestCase):
    
    def setUp(self):
        """准备符合最新架构 State 的测试数据"""
        self.valid_state: VideoSummaryState = {
            "transcript": "",
            "keyframes": [],
            "user_prompt": "",
            "draft_summary": "草稿：这里说有一只飞天猪。",
            "aggregated_chunk_insights": "听觉：没有提到飞天猪。\n视觉：画面里只有一棵树。",
            "structured_global_context": {
                "entities": [
                    {"name": "LangGraph", "kind": "observed_term", "frequency": 3},
                    {"name": "OpenAI",   "kind": "observed_term", "frequency": 2},
                ],
                "timeline_anchors": [
                    {"chunk_id": "chunk-000", "start_sec": 0,   "end_sec": 120},
                    {"chunk_id": "chunk-001", "start_sec": 120, "end_sec": 240},
                ],
            },
            "revision_count": 0,
            "hallucination_score": "",
            "usefulness_score": "",
            "feedback_instructions": ""
        }

    def test_short_circuit_empty_draft(self):
        """边界情况 1：草稿为空时，短路放行"""
        state = self.valid_state.copy()
        state["draft_summary"] = ""
        result = hallucination_grader_node(state)
        self.assertEqual(result["hallucination_score"], "no")
        self.assertEqual(result["feedback_instructions"], "")

    def test_short_circuit_max_revisions(self):
        """边界情况 2：达到最大重写次数，短路放行（防死循环）"""
        state = self.valid_state.copy()
        # [优化建议 1 落地]：消除魔法数字，直接使用从业务代码导入的常量 MAX_REVISIONS
        state["revision_count"] = MAX_REVISIONS
        result = hallucination_grader_node(state)
        self.assertEqual(result["hallucination_score"], "no")

    @patch.dict(os.environ, clear=True)
    def test_missing_api_key(self):
        """边界情况 3：缺少 API Key 报错"""
        with self.assertRaisesRegex(ValueError, ".*OPENAI_API_KEY.*"):
            hallucination_grader_node(self.valid_state)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123", "OPENAI_BASE_URL": "https://fake.url"})
    @patch('core.workflow.video_summary.nodes.hallucination_grader.get_model_for_capability')
    # [优化建议 3 确认]：由于 OpenAI 客户端是在节点函数内实例化(按需实例化)的，这里的 Mock 完全安全且生效。
    def test_grader_no_hallucination(self, mock_get_model):
        """一般情况 1：无幻觉，返回 score='no'"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        mock_model.chat_completion.return_value = json.dumps({
            "score": "no",
            "faulty_timestamp": "",
            "reason": ""
        })
        
        result = hallucination_grader_node(self.valid_state)
        
        mock_model.chat_completion.assert_called_once()
        
        # 验证是否开启了 JSON Mode
        call_kwargs = mock_model.chat_completion.call_args.kwargs
        self.assertEqual(call_kwargs.get("response_format"), {"type": "json_object"})
        
        self.assertEqual(result["hallucination_score"], "no")
        self.assertEqual(result["feedback_instructions"], "")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123", "OPENAI_BASE_URL": "https://fake.url"})
    @patch('core.workflow.video_summary.nodes.hallucination_grader.get_model_for_capability')
    def test_grader_yes_hallucination(self, mock_get_model):
        """一般情况 2：检测到幻觉，返回 score='yes' 及具体的反馈指令"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model

        mock_model.chat_completion.return_value = json.dumps({
            "score": "yes",
            "faulty_timestamp": "第一段",
            "reason": "源数据未提及飞天猪，属于捏造。"
        })
        
        result = hallucination_grader_node(self.valid_state)
        
        self.assertEqual(result["hallucination_score"], "yes")
        self.assertIn("发生位置 第一段", result["feedback_instructions"])
        self.assertIn("源数据未提及飞天猪", result["feedback_instructions"])

    @patch('builtins.print')
    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123"})
    @patch('core.workflow.video_summary.nodes.hallucination_grader.get_model_for_capability')
    def test_grader_api_or_json_error(self, mock_get_model, mock_print):
        """边界情况 4：API报错或 JSON 解析失败，降级为无幻觉 (no)，并严格验证降级日志记录"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        mock_model.chat_completion.return_value = "这是一段普通的文字，不是 JSON"
        
        result = hallucination_grader_node(self.valid_state)
        self.assertEqual(result["hallucination_score"], "no", "非 JSON 响应必须降级放行")
        
        # [优化建议 2 落地]：断言日志是否正确捕获并输出了 JSON 解析错误
        print_args = [call_args[0][0] for call_args in mock_print.call_args_list]
        json_error_logged = any("Error or Invalid JSON" in arg for arg in print_args)
        self.assertTrue(json_error_logged, "系统应当详细记录 JSON 解析失败的降级日志，以便排查")
        
        mock_print.reset_mock()
        
        # 模拟网络异常
        mock_model.chat_completion.side_effect = Exception("API Server Timeout")
        result2 = hallucination_grader_node(self.valid_state)
        self.assertEqual(result2["hallucination_score"], "no", "网络异常必须降级放行")

        # 断言网络超时等硬报错也被记录
        print_args_timeout = [call_args[0][0] for call_args in mock_print.call_args_list]
        timeout_error_logged = any("API Server Timeout" in arg for arg in print_args_timeout)
        self.assertTrue(timeout_error_logged, "系统应当详细记录 API 超时导致降级的日志，以便排查")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123"})
    @patch('core.workflow.video_summary.nodes.hallucination_grader.get_model_for_capability')
    def test_grader_injects_outline_context_into_llm_call(self, mock_get_model):
        """新增：structured_global_context 的实体与时间锚点应当被注入 LLM user_content"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        mock_model.chat_completion.return_value = json.dumps(
            {"score": "no", "faulty_timestamp": "", "reason": ""}
        )

        hallucination_grader_node(self.valid_state)

        call_kwargs = mock_model.chat_completion.call_args.kwargs
        messages = call_kwargs.get("messages", [])
        user_content = next(
            (m["content"] for m in messages if m.get("role") == "user"), ""
        )
        # 实体名称应出现在 user_content 里
        self.assertIn("LangGraph", user_content)
        self.assertIn("OpenAI",   user_content)
        # 时间锚点应出现
        self.assertIn("chunk-000", user_content)
        self.assertIn("chunk-001", user_content)
        # 二级约束标题应出现
        self.assertIn("二级约束", user_content)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123"})
    @patch('core.workflow.video_summary.nodes.hallucination_grader.get_model_for_capability')
    def test_grader_gracefully_handles_empty_outline(self, mock_get_model):
        """新增：structured_global_context 为空或缺失时不应崩溃，二级约束块不出现"""
        mock_model = MagicMock()
        mock_get_model.return_value = mock_model
        mock_model.chat_completion.return_value = json.dumps(
            {"score": "no", "faulty_timestamp": "", "reason": ""}
        )

        for empty_ctx in ({}, None, {"entities": [], "timeline_anchors": []}):
            state = {**self.valid_state, "structured_global_context": empty_ctx}
            result = hallucination_grader_node(state)
            self.assertEqual(result["hallucination_score"], "no")

            call_kwargs = mock_model.chat_completion.call_args.kwargs
            messages = call_kwargs.get("messages", [])
            user_content = next(
                (m["content"] for m in messages if m.get("role") == "user"), ""
            )
            self.assertNotIn("二级约束", user_content)


if __name__ == '__main__':
    unittest.main()