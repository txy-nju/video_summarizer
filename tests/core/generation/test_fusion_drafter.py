import unittest
from unittest.mock import patch, MagicMock
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 将项目根目录添加到 sys.path
project_root = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
sys.path.insert(0, str(project_root))

from core.workflow.video_summary.state import VideoSummaryState
from core.workflow.video_summary.nodes.fusion_drafter import fusion_drafter_node

env_path = project_root / '.env'
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
else:
    load_dotenv()

class TestFusionDrafterNode(unittest.TestCase):
    
    def setUp(self):
        """准备单元测试用的虚假 State"""
        self.valid_state: VideoSummaryState = {
            "transcript": "",
            "keyframes": [],
            "aggregated_chunk_insights": "## chunk-000 [00:00 - 00:30]\n### chunk_summary\n苹果发布了新款 iPhone。\n### audio_insights\n强调 A17 芯片性能。\n### vision_insights\n展示钛金属外壳。",
            "user_prompt": "侧重芯片性能",
            "draft_summary": "",
            "revision_count": 0,
            "feedback_instructions": "",
            "hallucination_score": "",
            "usefulness_score": ""
        }

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123", "OPENAI_BASE_URL": "https://fake.url"})
    @patch('core.workflow.video_summary.nodes.fusion_drafter.get_model_for_capability')
    def test_fusion_drafter_mocked_normal(self, mock_get_model):
        """单元测试：一般情况的融合组装，验证参数是否正确透传"""
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = "苹果发布了新款iPhone，搭载性能提升20%的A17芯片。"
        mock_get_model.return_value = mock_model
        
        result = fusion_drafter_node(self.valid_state)
        
        # 验证大模型调用
        mock_model.chat_completion.assert_called_once()
        self.assertEqual(result["revision_count"], 1, "重写次数应该加 1")
        self.assertEqual(result["draft_summary"], "苹果发布了新款iPhone，搭载性能提升20%的A17芯片。")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake_key_123", "OPENAI_BASE_URL": "https://fake.url"})
    @patch('core.workflow.video_summary.nodes.fusion_drafter.get_model_for_capability')
    def test_fusion_drafter_mocked_with_feedback(self, mock_get_model):
        """单元测试：存在 feedback_instructions 时的回流重写逻辑，验证 System Prompt 的动态修改"""
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = "修正版：添加了之前遗漏的关于钛金属的描述。"
        mock_get_model.return_value = mock_model
        
        state_with_feedback = self.valid_state.copy()
        state_with_feedback["revision_count"] = 1 # 假设已经是第1次重写失败，这是第2次尝试
        state_with_feedback["feedback_instructions"] = "你忘记提到钛金属外壳了！请务必加上。"
        
        result = fusion_drafter_node(state_with_feedback)
        
        # 验证提示词中是否成功注入了 feedback 和重写次数
        call_kwargs = mock_model.chat_completion.call_args.kwargs
        messages = call_kwargs.get("messages", [])
        system_msg = [msg["content"] for msg in messages if msg["role"] == "system"][0]
        
        self.assertIn("这是第 2 次重写草稿", system_msg)
        self.assertIn("你忘记提到钛金属外壳了！请务必加上", system_msg)
        self.assertEqual(result["revision_count"], 2, "重写次数应该加 1")

    def test_fusion_drafter_integration(self):
        """
        集成测试：真实的 API 级联调用验证。
        基于聚合节点产出的 aggregated_chunk_insights 执行真实 API 生成。
        并将最终草稿报告持久化保存到 test_output 目录供人工复核。
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            self.skipTest("OPENAI_API_KEY not found. Skipping integration test.")
            
        state: VideoSummaryState = {
            "transcript": "",
            "keyframes": [],
            "aggregated_chunk_insights": (
                "## chunk-000 [00:00 - 00:30]\n"
                "### chunk_summary\n"
                "视频开场介绍产品发布背景，强调新款手机定位。\n"
                "### audio_insights\n"
                "演讲者聚焦处理器性能与能效提升。\n"
                "### vision_insights\n"
                "PPT 展示性能曲线和材质对比图。\n\n"
                "## chunk-001 [00:30 - 01:00]\n"
                "### chunk_summary\n"
                "进入功能演示，突出实际使用体验。"
            ),
            "draft_summary": "",
            "user_prompt": "这是一次自动化的集成测试，请尽量简短地将画面和文字结合进行总结。",
            "revision_count": 0,
            "feedback_instructions": "",
            "hallucination_score": "",
            "usefulness_score": ""
        }

        # 1. 融合生成 Draft
        print("\n--- [Integration] Running Fusion Drafter Node ---")
        fusion_result = fusion_drafter_node(state)
        
        draft = fusion_result.get("draft_summary", "")
        print(f"\n====== [Generated Draft Summary] ======\n{draft}\n=======================================\n")
        
        # --- 4. 将产物持久化保存到独立目录 ---
        save_dir = project_root / "test_output" / "test_fusion_drafter_integration"
        save_dir.mkdir(parents=True, exist_ok=True)
        
        draft_path = save_dir / "draft_summary.md"
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write("# 分片聚合洞察 (Aggregated Chunk Insights)\n")
            f.write(state.get("aggregated_chunk_insights", "") + "\n\n")
            f.write("# 最终合成草稿 (Generated Draft)\n")
            f.write(draft)
            
        print(f"\n[Success] Execution artifacts saved to: {draft_path}")
        
        self.assertTrue(len(draft) > 20, "Draft summary should be successfully synthesized and not be empty.")
        self.assertEqual(fusion_result.get("revision_count"), 1)

if __name__ == '__main__':
    unittest.main()