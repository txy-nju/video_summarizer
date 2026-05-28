"""
Unit tests for the chunk subgraph audio -> vision sequential flow.

Verifies:
1. build_chunk_subgraph() compiles successfully.
2. _make_chunk_subgraph_node() wrapper executes audio then vision sequentially.
3. transcript_claims flows from audio worker into vision worker state.
4. Output contains chunk_results list with correct fields.
"""
import unittest
from unittest.mock import MagicMock, patch

from core.workflow.video_summary.graph import _make_chunk_subgraph_node, build_chunk_subgraph


class TestBuildChunkSubgraph(unittest.TestCase):

    def test_build_chunk_subgraph_compiles(self):
        sg = build_chunk_subgraph()
        self.assertEqual(type(sg).__name__, "CompiledStateGraph")


class TestChunkSubgraphNodeWrapper(unittest.TestCase):

    def _base_state(self, chunk_id="chunk-0"):
        return {
            "chunk_id": chunk_id,
            "transcript_segments": [{"start": 5, "end": 15, "text": "Hello world"}],
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
            "user_prompt": "unit-test",
            "trace_id": "t-001",
        }

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_audio_runs_before_vision(self, mock_audio_cap, mock_vision_cap):
        call_order = []

        audio_mock = MagicMock()
        def audio_side(**kw):
            call_order.append("audio")
            return '[{"claim": "hello", "exact_quote": "Hello world", "timestamp": "00:05"}]'
        audio_mock.chat_completion.side_effect = audio_side
        mock_audio_cap.return_value = audio_mock

        vision_mock = MagicMock()
        def vision_side(**kw):
            call_order.append("vision")
            return '{"frame_references": [], "chunk_summary": "done"}'
        vision_mock.chat_completion.side_effect = vision_side
        mock_vision_cap.return_value = vision_mock

        node_fn = _make_chunk_subgraph_node()
        node_fn(self._base_state())

        self.assertEqual(call_order, ["audio", "vision"])

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_transcript_claims_flows_from_audio_to_vision(self, mock_audio_cap, mock_vision_cap):
        audio_mock = MagicMock()
        audio_mock.chat_completion.return_value = (
            '[{"claim": "key fact", "exact_quote": "Hello world", "timestamp": "00:05"}]'
        )
        mock_audio_cap.return_value = audio_mock

        captured = {}
        vision_mock = MagicMock()
        def vision_side(**kw):
            messages = kw.get("messages", [])
            if len(messages) >= 2:
                parts = messages[1].get("content", [])
                captured["user"] = " ".join(
                    str(p.get("text", "")) for p in parts if isinstance(p, dict)
                )
            return '{"frame_references": [], "chunk_summary": "ok"}'
        vision_mock.chat_completion.side_effect = vision_side
        mock_vision_cap.return_value = vision_mock

        node_fn = _make_chunk_subgraph_node()
        node_fn(self._base_state())

        self.assertIn("transcript_claims", captured.get("user", ""))
        self.assertIn("key fact", captured.get("user", ""))

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_output_structure_is_correct(self, mock_audio_cap, mock_vision_cap):
        for cap in (mock_audio_cap, mock_vision_cap):
            m = MagicMock()
            m.chat_completion.return_value = '{"frame_references": [], "chunk_summary": ""}'
            cap.return_value = m

        a_mock = MagicMock()
        a_mock.chat_completion.return_value = "[]"
        mock_audio_cap.return_value = a_mock

        node_fn = _make_chunk_subgraph_node()
        result = node_fn(self._base_state(chunk_id="cX"))

        self.assertIn("chunk_results", result)
        items = result["chunk_results"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["chunk_id"], "cX")
        self.assertIn("transcript_claims", item)
        self.assertIn("frame_references", item)
        self.assertIn("chunk_summary", item)
        self.assertIn("modality_status", item)
        self.assertIn("latency_ms", item)

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_empty_chunk_id_returns_empty_results(self, mock_audio_cap, mock_vision_cap):
        node_fn = _make_chunk_subgraph_node()
        result = node_fn({"chunk_id": "", "transcript_segments": []})
        self.assertEqual(result, {"chunk_results": []})


if __name__ == "__main__":
    unittest.main()
