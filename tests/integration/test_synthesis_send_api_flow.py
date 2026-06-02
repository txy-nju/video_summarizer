"""
Integration tests for the new chunk subgraph flow (audio -> vision sequential pipeline).

Replaces the old synthesizer-based tests after Phase E removed chunk_synthesizer.py.
"""
import unittest
from typing import cast
from unittest.mock import MagicMock, patch

from core.workflow.video_summary.graph import _make_chunk_subgraph_node
from core.workflow.video_summary.state import _merge_chunk_results


class TestChunkSubgraphIntegration(unittest.TestCase):

    def _make_chunk_state(self, chunk_id="c1", **kw):
        base = {
            "chunk_id": chunk_id,
            "transcript_segments": [{"start": 0, "end": 30, "text": "demo segment"}],
            "keyframe_indexes": [0],
            "keyframes": [{"time": "00:05", "image": "aGVsbG8="}],
            "keyframes_base_path": "",
            "narrative_arc": [],
            "previous_chunk_summaries": [],
            "transcript_claims": [],
            "frame_references": [],
            "chunk_summary": "",
            "modality_status": {},
            "latency_ms": {},
            "user_prompt": "test focus",
            "trace_id": "trace-test",
        }
        base.update(kw)
        return base

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_vision_receives_transcript_claims_from_audio(self, mock_audio_model, mock_vision_model):
        """vision worker は audio worker の transcript_claims を受け取って処理できる。"""
        audio_mock = MagicMock()
        audio_mock.chat_completion.return_value = (
            '[{"claim": "demo mentioned", "exact_quote": "demo segment", "timestamp": "00:00"}]'
        )
        mock_audio_model.return_value = audio_mock

        captured_vision_input = {}

        def _vision_side_effect(**kwargs):
            messages = kwargs.get("messages", [])
            if len(messages) >= 2:
                parts = messages[1].get("content", [])
                joined = " ".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
                captured_vision_input["content"] = joined
            return '{"frame_references": [{"frame_time": "00:05", "observation": "ok", "audio_claim_match": "confirmed"}], "chunk_summary": "summary ok"}'

        vision_mock = MagicMock()
        vision_mock.chat_completion.side_effect = _vision_side_effect
        mock_vision_model.return_value = vision_mock

        node_fn = _make_chunk_subgraph_node()
        result = node_fn(self._make_chunk_state())

        # vision prompt should contain transcript_claims content
        self.assertIn("transcript_claims", captured_vision_input.get("content", ""))

        chunk_results = result.get("chunk_results", [])
        self.assertEqual(len(chunk_results), 1)
        item = chunk_results[0]
        self.assertEqual(item["chunk_id"], "c1")
        self.assertIsInstance(item["transcript_claims"], list)
        self.assertEqual(len(item["transcript_claims"]), 1)
        self.assertIsInstance(item["frame_references"], list)

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_chunk_summary_comes_from_vision_worker(self, mock_audio_model, mock_vision_model):
        """chunk_summary は vision worker が生成する（synthesizer ではない）。"""
        audio_mock = MagicMock()
        audio_mock.chat_completion.return_value = "[]"
        mock_audio_model.return_value = audio_mock

        vision_mock = MagicMock()
        vision_mock.chat_completion.return_value = (
            '{"frame_references": [], "chunk_summary": "vision-generated-summary"}'
        )
        mock_vision_model.return_value = vision_mock

        node_fn = _make_chunk_subgraph_node()
        result = node_fn(self._make_chunk_state(chunk_id="c2"))

        item = result["chunk_results"][0]
        self.assertEqual(item.get("chunk_summary"), "vision-generated-summary")

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_result_has_no_legacy_fields(self, mock_audio_model, mock_vision_model):
        """chunk_results に audio_insights / vision_insights が含まれないことを確認。"""
        for m in (mock_audio_model, mock_vision_model):
            mm = MagicMock()
            mm.chat_completion.return_value = '{"frame_references": [], "chunk_summary": ""}'
            m.return_value = mm

        audio_mm = MagicMock()
        audio_mm.chat_completion.return_value = "[]"
        mock_audio_model.return_value = audio_mm

        node_fn = _make_chunk_subgraph_node()
        result = node_fn(self._make_chunk_state(chunk_id="c3"))

        item = result["chunk_results"][0]
        self.assertNotIn("audio_insights", item)
        self.assertNotIn("vision_insights", item)

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_merge_reducer_integrates_subgraph_results(self, mock_audio_model, mock_vision_model):
        """_merge_chunk_results は subgraph 出力を既存 chunk_results にマージできる。"""
        for mock in (mock_audio_model, mock_vision_model):
            m = MagicMock()
            m.chat_completion.return_value = '{"frame_references": [], "chunk_summary": "s"}'
            mock.return_value = m

        audio_mm = MagicMock()
        audio_mm.chat_completion.return_value = "[]"
        mock_audio_model.return_value = audio_mm

        node_fn = _make_chunk_subgraph_node()
        result = node_fn(self._make_chunk_state(chunk_id="cx"))

        existing = [{"chunk_id": "cy", "chunk_summary": "other"}]
        merged = _merge_chunk_results(existing, result["chunk_results"])
        self.assertEqual(len(merged), 2)
        ids = {r["chunk_id"] for r in merged}
        self.assertIn("cx", ids)
        self.assertIn("cy", ids)


if __name__ == "__main__":
    unittest.main()
