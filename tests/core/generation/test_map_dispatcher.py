import unittest
from typing import cast

from core.workflow.video_summary.nodes.map_dispatcher import (
    map_dispatch_node,
    route_after_wave_synthesis,
    route_chunk_subgraph_tasks,
    wave_gate_node,
    ROUTE_CONTINUE_WAVE,
    ROUTE_WAVE_DONE,
)
from core.workflow.video_summary.state import VideoSummaryState


class TestMapDispatcherNode(unittest.TestCase):

    def test_map_dispatch_populates_retry_and_debug_info(self):
        chunk_results = [{"chunk_id": "chunk-000", "chunk_summary": "ok"}]
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [
                    {"chunk_id": "chunk-000", "start_sec": 0, "end_sec": 120},
                    {"chunk_id": "chunk-001", "start_sec": 120, "end_sec": 240},
                ],
                "chunk_results": chunk_results,
                "chunk_retry_count": {"chunk-000": 3},
                "reduce_debug_info": {"trace_id": "trace-1"},
            },
        )
        result = map_dispatch_node(state)
        self.assertEqual(result["chunk_retry_count"]["chunk-000"], 3)
        self.assertEqual(result["chunk_retry_count"]["chunk-001"], 0)
        self.assertTrue(result["reduce_debug_info"]["dispatch_ready"])
        self.assertEqual(result["reduce_debug_info"]["chunk_count"], 2)
        self.assertEqual(result["reduce_debug_info"]["dispatch_strategy"], "send-api-wave-pilot")
        self.assertEqual(result["reduce_debug_info"]["trace_id"], "trace-1")
        self.assertEqual(result["active_wave_chunk_ids"], ["chunk-001"])
        self.assertEqual(result["wave_index"], 0)
        self.assertIs(result["chunk_results"], chunk_results)

    def test_map_dispatch_marks_send_api_strategy(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [{"chunk_id": "chunk-000", "start_sec": 0, "end_sec": 120}],
                "chunk_results": [],
            },
        )
        result = map_dispatch_node(state)
        self.assertEqual(result["reduce_debug_info"]["dispatch_strategy"], "send-api-wave-pilot")

    def test_map_dispatch_handles_invalid_types(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": "invalid",
                "chunk_retry_count": "invalid",
                "reduce_debug_info": "invalid",
                "chunk_results": "invalid-results",
            },
        )
        result = map_dispatch_node(state)
        self.assertEqual(result["chunk_retry_count"], {})
        self.assertEqual(result["reduce_debug_info"]["chunk_count"], 0)
        self.assertTrue(result["reduce_debug_info"]["dispatch_ready"])
        self.assertEqual(result["active_wave_chunk_ids"], [])
        self.assertEqual(result["chunk_results"], [])

    def test_map_dispatch_builds_sliding_window_chunk_summary_memory(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [
                    {"chunk_id": "chunk-000", "start_sec": 0, "end_sec": 10},
                    {"chunk_id": "chunk-001", "start_sec": 10, "end_sec": 20},
                    {"chunk_id": "chunk-002", "start_sec": 20, "end_sec": 30},
                    {"chunk_id": "chunk-003", "start_sec": 30, "end_sec": 40},
                ],
                "chunk_results": [
                    {"chunk_id": "chunk-000", "chunk_summary": "  first summary  "},
                    {"chunk_id": "chunk-001", "chunk_summary": "second summary"},
                    {"chunk_id": "chunk-002", "chunk_summary": "third summary"},
                ],
            },
        )
        result = map_dispatch_node(state)
        self.assertEqual(result.get("active_wave_chunk_ids"), ["chunk-003"])
        memory = result.get("chunk_summary_memory", {})
        self.assertEqual(memory.get("chunk-000"), "first summary")
        self.assertEqual(memory.get("chunk-001"), "second summary")
        self.assertEqual(memory.get("chunk-002"), "third summary")
        previous_map = result.get("previous_chunk_summaries_by_chunk", {})
        previous_for_target = previous_map.get("chunk-003", [])
        self.assertEqual(
            [item.get("chunk_id") for item in previous_for_target], ["chunk-001", "chunk-002"]
        )

    # ── route_chunk_subgraph_tasks ────────────────────────────────────────────

    def test_route_chunk_subgraph_tasks_builds_send_payload(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [
                    {"chunk_id": "chunk-000", "start_sec": 0, "end_sec": 60,
                     "transcript_segment_indexes": [0], "keyframe_indexes": [0]},
                    {"chunk_id": "chunk-001", "start_sec": 60, "end_sec": 120,
                     "transcript_segment_indexes": [1], "keyframe_indexes": [1]},
                ],
                "active_wave_chunk_ids": ["chunk-001"],
                "transcript": '{"segments": [{"start": 0, "end": 30, "text": "x"}, {"start": 60, "end": 90, "text": "y"}]}',
                "keyframes": [{"time": "00:05", "image": "a"}, {"time": "01:05", "image": "b"}],
                "keyframes_base_path": "./frames",
                "user_prompt": "test focus",
                "narrative_arc": [{"chapter_id": "ch1", "title": "Intro", "start_sec": 0, "end_sec": 120}],
                "chunk_results": [{"chunk_id": "chunk-000", "chunk_summary": "done"}],
                "previous_chunk_summaries_by_chunk": {},
                "trace_id": "trace-abc",
            },
        )
        sends = route_chunk_subgraph_tasks(state)
        self.assertEqual(len(sends), 1)
        self.assertEqual(getattr(sends[0], "node", ""), "chunk_subgraph_node")
        payload = getattr(sends[0], "arg", {})
        self.assertEqual(payload.get("chunk_id"), "chunk-001")
        self.assertEqual(payload.get("user_prompt"), "test focus")
        self.assertEqual(payload.get("trace_id"), "trace-abc")
        self.assertIsInstance(payload.get("transcript_segments"), list)
        self.assertIsInstance(payload.get("narrative_arc"), list)

    def test_route_chunk_subgraph_tasks_skips_done_chunks(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [
                    {"chunk_id": "c1", "transcript_segment_indexes": [], "keyframe_indexes": []},
                    {"chunk_id": "c2", "transcript_segment_indexes": [], "keyframe_indexes": []},
                ],
                "active_wave_chunk_ids": ["c1", "c2"],
                "transcript": "{}",
                "keyframes": [],
                "keyframes_base_path": "",
                "user_prompt": "",
                "narrative_arc": [],
                "chunk_results": [{"chunk_id": "c1", "chunk_summary": "already done"}],
                "previous_chunk_summaries_by_chunk": {},
            },
        )
        sends = route_chunk_subgraph_tasks(state)
        self.assertEqual(len(sends), 1)
        self.assertEqual(getattr(sends[0], "arg", {}).get("chunk_id"), "c2")

    # ── wave_gate_node ────────────────────────────────────────────────────────

    def test_wave_gate_node_reorders_by_chunk_plan(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [
                    {"chunk_id": "c1", "start_sec": 0, "end_sec": 60},
                    {"chunk_id": "c2", "start_sec": 60, "end_sec": 120},
                    {"chunk_id": "c3", "start_sec": 120, "end_sec": 180},
                ],
                "chunk_results": [
                    {"chunk_id": "c3", "chunk_summary": "third"},
                    {"chunk_id": "c1", "chunk_summary": "first"},
                    {"chunk_id": "c2", "chunk_summary": "second"},
                ],
                "reduce_debug_info": {},
            },
        )
        result = wave_gate_node(state)
        ordered = result.get("chunk_results", [])
        self.assertEqual([r["chunk_id"] for r in ordered], ["c1", "c2", "c3"])

    # ── route_after_wave_synthesis ────────────────────────────────────────────

    def test_route_after_wave_synthesis_continue_when_pending(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
                "chunk_results": [{"chunk_id": "c1", "chunk_summary": "done"}],
            },
        )
        self.assertEqual(route_after_wave_synthesis(state), ROUTE_CONTINUE_WAVE)

    def test_route_after_wave_synthesis_done_when_all_synthesized(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
                "chunk_results": [
                    {"chunk_id": "c1", "chunk_summary": "done-1"},
                    {"chunk_id": "c2", "chunk_summary": "done-2"},
                ],
            },
        )
        self.assertEqual(route_after_wave_synthesis(state), ROUTE_WAVE_DONE)

    def test_route_after_wave_synthesis_done_when_terminal_without_summary(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [{"chunk_id": "c1"}],
                "chunk_results": [
                    {"chunk_id": "c1", "modality_status": {"vision": "failed"}, "chunk_summary": ""}
                ],
            },
        )
        self.assertEqual(route_after_wave_synthesis(state), ROUTE_WAVE_DONE)


if __name__ == "__main__":
    unittest.main()
