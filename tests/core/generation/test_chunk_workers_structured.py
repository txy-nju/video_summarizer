import unittest
from typing import Any, Dict, cast
from unittest.mock import MagicMock
from unittest.mock import patch

from core.workflow.video_summary.nodes.chunk_audio_analyzer import chunk_audio_worker_node
from core.workflow.video_summary.nodes.chunk_vision_analyzer import chunk_vision_worker_node
from core.workflow.video_summary.state import VideoSummaryState


class TestChunkWorkersStructuredOutput(unittest.TestCase):
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_audio_llm_prompt_contains_evidence_tier_rules(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = '{"observation":{"source":"direct_audio","content":"提到 LangGraph"},"context_calibration":{"source":"structured_global_context","content":"术语消歧"},"final_summary":"该分片讨论 LangGraph"}'
        mock_get_model.return_value = mock_model

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL_NAME": "gpt-4o"}, clear=False):
            state = cast(
                VideoSummaryState,
                {
                    "current_chunk": {"chunk_id": "chunk-004", "transcript_segment_indexes": [0]},
                    "transcript": '{"segments": [{"text": "LangGraph orchestration explained"}]}',
                    "user_prompt": "关注术语",
                    "structured_global_context": {"entities": [{"name": "LangGraph"}]},
                    "previous_chunk_summaries": [{"chunk_id": "chunk-003", "summary": "上一分片提到 orchestration"}],
                },
            )
            chunk_audio_worker_node(state)

        call_kwargs = mock_model.chat_completion.call_args.kwargs
        messages = call_kwargs.get("messages", [])
        self.assertTrue(messages)

        system_content = str(messages[0].get("content", ""))
        self.assertIn("一级证据 (observation)", system_content)
        self.assertIn("二级证据 (context_calibration)", system_content)
        self.assertIn("绝对禁止用大纲来捏造", system_content)
        self.assertIn("证据不足", system_content)

        user_content = str(messages[1].get("content", ""))
        self.assertIn("[previous_chunk_summaries]", user_content)

    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer._search_with_cache", return_value="cached")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer._llm_audio_chunk_structured")
    def test_audio_worker_returns_structured_and_compat_fields(self, mock_llm, _mock_cache):
        mock_llm.return_value = {
            "observation": {"source": "direct_audio", "content": "提到了 LangGraph"},
            "context_calibration": {"source": "structured_global_context", "content": "和全局实体对齐"},
            "final_summary": "该分片主要讲 LangGraph 编排。",
        }

        state = cast(
            VideoSummaryState,
            {
                "current_chunk": {"chunk_id": "chunk-000", "transcript_segment_indexes": [0]},
                "transcript": '{"segments": [{"text": "LangGraph worker orchestration"}]}',
                "user_prompt": "关注技术术语",
                "structured_global_context": {"entities": [{"name": "LangGraph"}]},
            },
        )

        result = chunk_audio_worker_node(state)
        item = result["chunk_results"][0]

        self.assertIn("audio_structured_analysis", item)
        self.assertEqual(item["audio_structured_analysis"]["final_summary"], "该分片主要讲 LangGraph 编排。")
        self.assertEqual(item["audio_insights"], "该分片主要讲 LangGraph 编排。")

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer._search_with_cache", return_value="cached")
    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer._llm_vision_chunk_structured")
    def test_vision_worker_returns_structured_and_compat_fields(self, mock_llm, _mock_cache):
        mock_llm.return_value = {
            "observation": {"source": "direct_vision", "content": "出现代码编辑器与终端画面"},
            "context_calibration": {"source": "structured_global_context", "content": "与全局时间锚点一致"},
            "final_summary": "该分片展示代码实操过程。",
        }

        state = cast(
            VideoSummaryState,
            {
                "current_chunk": {"chunk_id": "chunk-001", "keyframe_indexes": [0]},
                "keyframes": [{"time": "00:05", "image": "aGVsbG8="}],
                "keyframes_base_path": "",
                "user_prompt": "关注画面变化",
                "structured_global_context": {"timeline_anchors": [{"chunk_id": "chunk-001"}]},
            },
        )

        result = chunk_vision_worker_node(state)
        item = result["chunk_results"][0]

        self.assertIn("vision_structured_analysis", item)
        self.assertEqual(item["vision_structured_analysis"]["final_summary"], "该分片展示代码实操过程。")
        self.assertEqual(item["vision_insights"], "该分片展示代码实操过程。")

    def test_audio_worker_fallback_path_keeps_structured_contract(self):
        state = cast(
            VideoSummaryState,
            {
                "current_chunk": {"chunk_id": "chunk-002", "transcript_segment_indexes": []},
                "transcript": "{}",
                "user_prompt": "",
                "structured_global_context": {},
            },
        )

        result = chunk_audio_worker_node(state)
        item: Dict[str, Any] = result["chunk_results"][0]
        self.assertIn("audio_structured_analysis", item)
        self.assertIn("observation", item["audio_structured_analysis"])
        self.assertIn("context_calibration", item["audio_structured_analysis"])
        self.assertIn("final_summary", item["audio_structured_analysis"])

    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer._llm_audio_chunk_structured", side_effect=TimeoutError("request timeout"))
    def test_audio_worker_timeout_degrades_without_blocking(self, _mock_llm):
        state = cast(
            VideoSummaryState,
            {
                "current_chunk": {"chunk_id": "chunk-timeout-a", "transcript_segment_indexes": [0]},
                "transcript": '{"segments": [{"text": "audio evidence"}]}',
                "user_prompt": "",
                "structured_global_context": {},
            },
        )

        result = chunk_audio_worker_node(state)
        item = result["chunk_results"][0]
        self.assertEqual(item.get("modality_status", {}).get("audio"), "timeout")
        self.assertIn("<missing_context>", str(item.get("audio_insights", "")))
        self.assertTrue(item.get("degraded_context", {}).get("audio"))

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    def test_vision_llm_prompt_contains_evidence_tier_rules(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = '{"observation":{"source":"direct_vision","content":"画面出现IDE"},"context_calibration":{"source":"structured_global_context","content":"术语对齐"},"final_summary":"展示编码过程"}'
        mock_get_model.return_value = mock_model

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_VISION_MODEL_NAME": "gpt-4o"}, clear=False):
            state = cast(
                VideoSummaryState,
                {
                    "current_chunk": {"chunk_id": "chunk-003", "keyframe_indexes": [0]},
                    "keyframes": [{"time": "00:10", "image": "aGVsbG8="}],
                    "keyframes_base_path": "",
                    "user_prompt": "关注动作",
                    "structured_global_context": {"entities": [{"name": "IDE"}]},
                    "previous_chunk_summaries": [{"chunk_id": "chunk-002", "summary": "上一分片展示终端"}],
                },
            )
            chunk_vision_worker_node(state)

        call_kwargs = mock_model.chat_completion.call_args.kwargs
        messages = call_kwargs.get("messages", [])
        self.assertTrue(messages)

        system_content = str(messages[0].get("content", ""))
        self.assertIn("一级证据 (observation)", system_content)
        self.assertIn("二级证据 (context_calibration)", system_content)
        self.assertIn("绝对禁止用大纲来捏造", system_content)
        self.assertIn("证据不足", system_content)

        user_content = messages[1].get("content", [])
        text_blocks = [str(item.get("text", "")) for item in user_content if isinstance(item, dict) and item.get("type") == "text"]
        joined = "\n".join(text_blocks)
        self.assertIn("[previous_chunk_summaries]", joined)

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer._llm_vision_chunk_structured", side_effect=TimeoutError("request timeout"))
    def test_vision_worker_timeout_degrades_without_blocking(self, _mock_llm):
        state = cast(
            VideoSummaryState,
            {
                "current_chunk": {"chunk_id": "chunk-timeout-v", "keyframe_indexes": [0]},
                "keyframes": [{"time": "00:05", "image": "aGVsbG8="}],
                "keyframes_base_path": "",
                "user_prompt": "",
                "structured_global_context": {},
            },
        )

        result = chunk_vision_worker_node(state)
        item = result["chunk_results"][0]
        self.assertEqual(item.get("modality_status", {}).get("vision"), "timeout")
        self.assertIn("<missing_context>", str(item.get("vision_insights", "")))
        self.assertTrue(item.get("degraded_context", {}).get("vision"))


if __name__ == "__main__":
    unittest.main()