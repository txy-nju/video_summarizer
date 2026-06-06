import unittest
from typing import cast
from unittest.mock import MagicMock, patch

from core.workflow.video_summary.nodes.outline_bootstrap import outline_bootstrap_node
from core.workflow.video_summary.state import VideoSummaryState

_VALID_TRANSCRIPT = (
    '{"segments": [' 
    '{"start": 0, "end": 8, "text": "OpenAI launches GPT4o in Beijing"}, '
    '{"start": 9, "end": 18, "text": "随后团队演示 LangGraph workflow"}' 
    ']}' 
)

_VALID_CHUNK_PLAN = [
    {
        "chunk_id": "chunk-000",
        "start_sec": 0,
        "end_sec": 60,
        "transcript_segment_indexes": [0, 1],
        "keyframe_indexes": [0],
    }
]

_NARRATIVE_ARC_RESPONSE = (
    '[{"chapter_id": "ch1", "title": "Introduction", "start_sec": 0, "end_sec": 18, "summary": "OpenAI demo"}]'
)


class TestOutlineBootstrapNode(unittest.TestCase):

    @patch("core.workflow.video_summary.nodes.outline_bootstrap.get_model_for_capability")
    def test_narrative_arc_written_when_llm_succeeds(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = _NARRATIVE_ARC_RESPONSE
        mock_get_model.return_value = mock_model

        state = cast(VideoSummaryState, {"transcript": _VALID_TRANSCRIPT, "chunk_plan": _VALID_CHUNK_PLAN})
        result = outline_bootstrap_node(state)

        arc = result.get("narrative_arc", [])
        self.assertIsInstance(arc, list)
        self.assertGreater(len(arc), 0)
        chapter = arc[0]
        self.assertIn("chapter_id", chapter)
        self.assertIn("title", chapter)
        self.assertIn("start_sec", chapter)
        self.assertIn("end_sec", chapter)

    @patch("core.workflow.video_summary.nodes.outline_bootstrap.get_model_for_capability")
    def test_narrative_arc_degrades_to_empty_when_llm_fails(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.side_effect = Exception("LLM unavailable")
        mock_get_model.return_value = mock_model

        state = cast(VideoSummaryState, {"transcript": _VALID_TRANSCRIPT, "chunk_plan": _VALID_CHUNK_PLAN})
        result = outline_bootstrap_node(state)

        arc = result.get("narrative_arc", "MISSING")
        self.assertIsInstance(arc, list)
        self.assertEqual(arc, [])

    @patch("core.workflow.video_summary.nodes.outline_bootstrap.get_model_for_capability")
    def test_narrative_arc_degrades_to_empty_when_llm_returns_invalid_json(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = "not json at all"
        mock_get_model.return_value = mock_model

        state = cast(VideoSummaryState, {"transcript": _VALID_TRANSCRIPT, "chunk_plan": _VALID_CHUNK_PLAN})
        result = outline_bootstrap_node(state)

        arc = result.get("narrative_arc", "MISSING")
        self.assertIsInstance(arc, list)
        self.assertEqual(arc, [])

    @patch("core.workflow.video_summary.nodes.outline_bootstrap.get_model_for_capability")
    def test_reduce_debug_info_records_chapter_count(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = _NARRATIVE_ARC_RESPONSE
        mock_get_model.return_value = mock_model

        state = cast(VideoSummaryState, {"transcript": _VALID_TRANSCRIPT, "chunk_plan": _VALID_CHUNK_PLAN})
        result = outline_bootstrap_node(state)

        debug = result.get("reduce_debug_info", {})
        self.assertTrue(debug.get("outline_bootstrap_ready"))
        self.assertIn("outline_narrative_arc_chapter_count", debug)
        self.assertGreater(debug["outline_narrative_arc_chapter_count"], 0)

    @patch("core.workflow.video_summary.nodes.outline_bootstrap.get_model_for_capability")
    def test_handles_invalid_transcript_without_raising(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = "[]"
        mock_get_model.return_value = mock_model

        result = outline_bootstrap_node(
            cast(VideoSummaryState, {"transcript": "{bad-json}", "chunk_plan": "invalid"})
        )
        arc = result.get("narrative_arc", "MISSING")
        self.assertIsInstance(arc, list)


if __name__ == "__main__":
    unittest.main()
