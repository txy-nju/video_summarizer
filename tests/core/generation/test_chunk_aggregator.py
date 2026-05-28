import unittest

from core.workflow.video_summary.nodes.chunk_aggregator import chunk_aggregator_node


class TestChunkAggregatorNode(unittest.TestCase):
    def test_aggregates_by_chunk_plan_order(self):
        state = {
            "user_prompt": "重点关注性能",
            "chunk_plan": [
                {"chunk_id": "chunk-000", "start_sec": 0, "end_sec": 10},
                {"chunk_id": "chunk-001", "start_sec": 10, "end_sec": 20},
            ],
            "chunk_results": [
                {"chunk_id": "chunk-001", "audio_insights": "A1", "vision_insights": "V1", "chunk_summary": "S1"},
                {"chunk_id": "chunk-000", "audio_insights": "A0", "vision_insights": "V0", "chunk_summary": "S0"},
            ],
            "reduce_debug_info": {},
        }

        result = chunk_aggregator_node(state)  # type: ignore[arg-type]
        aggregated = result.get("aggregated_chunk_insights", "")

        self.assertIn("# Chunk Aggregated Insights", aggregated)
        self.assertTrue(aggregated.find("## chunk-000") < aggregated.find("## chunk-001"))
        self.assertIn("S0", aggregated)
        self.assertIn("S1", aggregated)

    def test_skips_empty_chunks_and_marks_debug(self):
        state = {
            "chunk_plan": [{"chunk_id": "chunk-000", "start_sec": 0, "end_sec": 10}],
            "chunk_results": [{"chunk_id": "chunk-000"}],
            "reduce_debug_info": {},
        }

        result = chunk_aggregator_node(state)  # type: ignore[arg-type]
        debug_info = result.get("reduce_debug_info", {})
        aggregated = result.get("aggregated_chunk_insights", "")

        self.assertEqual(debug_info.get("aggregator_total_chunks"), 1)
        self.assertEqual(debug_info.get("aggregator_dropped_chunks"), 1)
        self.assertIn("total_chunks: 1", aggregated)

    def test_truncates_when_content_too_long(self):
        # Phase G1 raised the default limit from 24000 to 100000
        very_long = "X" * 110000
        state = {
            "chunk_plan": [{"chunk_id": "chunk-000", "start_sec": 0, "end_sec": 10}],
            "chunk_results": [{"chunk_id": "chunk-000", "chunk_summary": very_long}],
            "reduce_debug_info": {},
        }

        result = chunk_aggregator_node(state)  # type: ignore[arg-type]
        aggregated = result.get("aggregated_chunk_insights", "")

        self.assertLessEqual(len(aggregated), 100000)
        self.assertIn("已自动截断", aggregated)


if __name__ == "__main__":
    unittest.main()
