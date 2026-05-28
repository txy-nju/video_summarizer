import unittest
from unittest.mock import MagicMock, patch


from core.workflow.video_summary.nodes.chunk_audio_analyzer import chunk_audio_worker_node
from core.workflow.video_summary.nodes.chunk_vision_analyzer import chunk_vision_worker_node


def _audio_state(chunk_id="chunk-0", **kw):
    base = {
        "chunk_id": chunk_id,
        "transcript_segments": [{"start": 10, "end": 20, "text": "LangGraph explained"}],
        "keyframe_indexes": [],
        "keyframes": [],
        "keyframes_base_path": "",
        "narrative_arc": [{"chapter_id": "ch1", "title": "Intro", "start_sec": 0, "end_sec": 60}],
        "previous_chunk_summaries": [],
        "transcript_claims": [],
        "frame_references": [],
        "chunk_summary": "",
        "modality_status": {},
        "latency_ms": {},
    }
    base.update(kw)
    return base


def _vision_state(chunk_id="chunk-0", **kw):
    base = {
        "chunk_id": chunk_id,
        "transcript_segments": [],
        "keyframe_indexes": [0],
        "keyframes": [{"time": "00:10", "image": "aGVsbG8="}],
        "keyframes_base_path": "",
        "narrative_arc": [{"chapter_id": "ch1", "title": "Intro", "start_sec": 0, "end_sec": 60}],
        "previous_chunk_summaries": [],
        "transcript_claims": [
            {"claim": "LangGraph explained", "exact_quote": "LangGraph explained", "timestamp": "00:10"}
        ],
        "frame_references": [],
        "chunk_summary": "",
        "modality_status": {},
        "latency_ms": {},
    }
    base.update(kw)
    return base


class TestChunkWorkersStructuredOutput(unittest.TestCase):

    # ── audio worker ──────────────────────────────────────────────────────────

    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_audio_prompt_injects_narrative_arc(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = "[]"
        mock_get_model.return_value = mock_model

        chunk_audio_worker_node(_audio_state())

        messages = mock_model.chat_completion.call_args.kwargs.get("messages", [])
        user_content = str(messages[1].get("content", ""))
        self.assertIn("narrative_arc", user_content)
        self.assertIn("Intro", user_content)

    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_audio_worker_outputs_transcript_claims(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = (
            '[{"claim": "LangGraph is mentioned", "exact_quote": "LangGraph explained", "timestamp": "00:10"}]'
        )
        mock_get_model.return_value = mock_model

        result = chunk_audio_worker_node(_audio_state())

        claims = result.get("transcript_claims", [])
        self.assertIsInstance(claims, list)
        self.assertEqual(len(claims), 1)
        self.assertIn("claim", claims[0])
        self.assertIn("exact_quote", claims[0])
        self.assertIn("timestamp", claims[0])

    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_audio_worker_has_no_legacy_fields(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = "[]"
        mock_get_model.return_value = mock_model

        result = chunk_audio_worker_node(_audio_state())

        self.assertNotIn("audio_insights", result)
        self.assertNotIn("audio_structured_analysis", result)
        self.assertNotIn("chunk_results", result)

    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_audio_worker_records_modality_status_and_latency(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = "[]"
        mock_get_model.return_value = mock_model

        result = chunk_audio_worker_node(_audio_state())

        self.assertIn("audio", result.get("modality_status", {}))
        self.assertIn("audio", result.get("latency_ms", {}))

    @patch("core.workflow.video_summary.nodes.chunk_audio_analyzer.get_model_for_capability")
    def test_audio_worker_timeout_degrades_gracefully(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.side_effect = TimeoutError("timeout")
        mock_get_model.return_value = mock_model

        result = chunk_audio_worker_node(_audio_state(chunk_id="chunk-timeout"))

        self.assertEqual(result.get("modality_status", {}).get("audio"), "timeout")
        self.assertIsInstance(result.get("transcript_claims", []), list)

    # ── vision worker ─────────────────────────────────────────────────────────

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    def test_vision_prompt_injects_transcript_claims_and_narrative_arc(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = '{"frame_references": [], "chunk_summary": "test"}'
        mock_get_model.return_value = mock_model

        chunk_vision_worker_node(_vision_state())

        messages = mock_model.chat_completion.call_args.kwargs.get("messages", [])
        user_parts = messages[1].get("content", [])
        joined = " ".join(str(p.get("text", "")) for p in user_parts if isinstance(p, dict))
        self.assertIn("narrative_arc", joined)
        self.assertIn("transcript_claims", joined)

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    def test_vision_worker_outputs_frame_references_and_chunk_summary(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = (
            '{"frame_references": [{"frame_time": "00:10", "observation": "speaker visible", "audio_claim_match": "confirmed"}], '
            '"chunk_summary": "LangGraph overview"}'
        )
        mock_get_model.return_value = mock_model

        result = chunk_vision_worker_node(_vision_state())

        refs = result.get("frame_references", [])
        self.assertIsInstance(refs, list)
        self.assertEqual(len(refs), 1)
        self.assertIn("frame_time", refs[0])
        self.assertIn("observation", refs[0])
        self.assertIn("audio_claim_match", refs[0])
        self.assertIsInstance(result.get("chunk_summary"), str)

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    def test_vision_worker_has_no_legacy_fields(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.return_value = '{"frame_references": [], "chunk_summary": ""}'
        mock_get_model.return_value = mock_model

        result = chunk_vision_worker_node(_vision_state())

        self.assertNotIn("vision_insights", result)
        self.assertNotIn("vision_structured_analysis", result)
        self.assertNotIn("chunk_results", result)

    @patch("core.workflow.video_summary.nodes.chunk_vision_analyzer.get_model_for_capability")
    def test_vision_worker_timeout_degrades_gracefully(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.chat_completion.side_effect = TimeoutError("timeout")
        mock_get_model.return_value = mock_model

        result = chunk_vision_worker_node(_vision_state(chunk_id="chunk-timeout-v"))

        self.assertEqual(result.get("modality_status", {}).get("vision"), "timeout")


if __name__ == "__main__":
    unittest.main()
