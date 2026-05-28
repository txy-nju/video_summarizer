import unittest
from typing import Any, Dict, List

from core.workflow.video_summary.state import _merge_chunk_results


class TestChunkResultsMerger(unittest.TestCase):

    def test_merge_empty_base_returns_update(self):
        base: List[Dict[str, Any]] = []
        update = [
            {"chunk_id": "c1", "transcript_claims": [{"claim": "x"}]},
            {"chunk_id": "c2", "transcript_claims": [{"claim": "y"}]},
        ]
        result = _merge_chunk_results(base, update)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["chunk_id"], "c1")
        self.assertEqual(result[1]["chunk_id"], "c2")

    def test_merge_empty_update_returns_base(self):
        base = [
            {"chunk_id": "c1", "frame_references": [{"observation": "scene"}]},
            {"chunk_id": "c2", "frame_references": []},
        ]
        update: List[Dict[str, Any]] = []
        result = _merge_chunk_results(base, update)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["frame_references"], [{"observation": "scene"}])

    def test_merge_overlapping_chunks_combines_fields(self):
        base = [{"chunk_id": "c1", "transcript_claims": [{"claim": "audio fact"}]}]
        update = [{"chunk_id": "c1", "frame_references": [{"observation": "visual fact"}]}]
        result = _merge_chunk_results(base, update)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["transcript_claims"], [{"claim": "audio fact"}])
        self.assertEqual(result[0]["frame_references"], [{"observation": "visual fact"}])

    def test_merge_latency_ms_recursive_merge(self):
        base = [{"chunk_id": "c1", "latency_ms": {"audio": 100}}]
        update = [{"chunk_id": "c1", "latency_ms": {"vision": 150}}]
        result = _merge_chunk_results(base, update)
        self.assertEqual(len(result), 1)
        latency = result[0]["latency_ms"]
        self.assertIn("audio", latency)
        self.assertIn("vision", latency)

    def test_merge_preserves_base_order(self):
        base = [
            {"chunk_id": "c1", "priority": 1},
            {"chunk_id": "c2", "priority": 2},
            {"chunk_id": "c3", "priority": 3},
        ]
        update = [
            {"chunk_id": "c3", "new_field": "updated_c3"},
            {"chunk_id": "c1", "new_field": "updated_c1"},
        ]
        result = _merge_chunk_results(base, update)
        self.assertEqual([item["chunk_id"] for item in result], ["c1", "c2", "c3"])
        self.assertEqual(result[0]["new_field"], "updated_c1")
        self.assertEqual(result[2]["new_field"], "updated_c3")


if __name__ == "__main__":
    unittest.main()
