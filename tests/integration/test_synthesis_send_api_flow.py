import unittest
from typing import Any, Dict, List, cast
from unittest.mock import patch

from core.workflow.video_summary.nodes.chunk_synthesizer import (
    chunk_synthesizer_node,
    chunk_synthesizer_worker_node,
)
from core.workflow.video_summary.nodes.map_dispatcher import (
    route_synthesis_send_tasks,
    synthesis_barrier_node,
)
from core.workflow.video_summary.state import VideoSummaryState, _merge_chunk_results


class TestSynthesisSendApiFlow(unittest.TestCase):
    def test_synthesis_route_waits_until_all_audio_vision_ready(self):
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
                "user_prompt": "focus",
                "chunk_results": [
                    {"chunk_id": "c1", "audio_insights": "a1", "vision_insights": "v1"},
                    {"chunk_id": "c2", "audio_insights": "a2"},
                ],
                "reduce_debug_info": {},
            },
        )

        barrier_update = synthesis_barrier_node(state)
        barrier_debug = barrier_update.get("reduce_debug_info", {})
        self.assertFalse(barrier_debug.get("synthesis_ready"))

        sends = route_synthesis_send_tasks(state)
        self.assertEqual(sends, [])

    @patch("core.workflow.video_summary.nodes.chunk_synthesizer._process_single_chunk_synthesis")
    def test_synthesis_send_worker_flow_generates_chunk_summary(self, mock_process):
        chunk_plan: List[Dict[str, Any]] = [{"chunk_id": "c1"}, {"chunk_id": "c2"}]
        state = cast(
            VideoSummaryState,
            {
                "chunk_plan": chunk_plan,
                "user_prompt": "focus",
                "structured_global_context": {"entities": [{"name": "demo"}]},
                "chunk_results": [
                    {"chunk_id": "c1", "audio_insights": "a1", "vision_insights": "v1"},
                    {"chunk_id": "c2", "audio_insights": "a2", "vision_insights": "v2"},
                ],
                "reduce_debug_info": {},
            },
        )

        def _mock_process(
            chunk_id: str,
            user_prompt: str,
            structured_global_context: str,
            base_item: Dict[str, Any],
        ):
            merged = dict(base_item)
            merged["chunk_summary"] = f"summary-{chunk_id}"
            merged["_debug_context"] = structured_global_context
            return chunk_id, merged

        mock_process.side_effect = _mock_process

        barrier_update = synthesis_barrier_node(state)
        barrier_debug = barrier_update.get("reduce_debug_info", {})
        self.assertTrue(barrier_debug.get("synthesis_ready"))

        sends = route_synthesis_send_tasks(state)
        self.assertEqual(len(sends), 2)

        worker_updates: List[Dict[str, Any]] = []
        for send in sends:
            payload = getattr(send, "arg", {})
            worker_state = cast(
                VideoSummaryState,
                {
                    "user_prompt": payload.get("user_prompt", ""),
                    "structured_global_context": payload.get("structured_global_context", ""),
                    "current_synthesis_chunk": payload.get("current_synthesis_chunk", {}),
                    "current_synthesis_base_item": payload.get("current_synthesis_base_item", {}),
                },
            )
            worker_updates.append(chunk_synthesizer_worker_node(worker_state))

        merged_results: List[Dict[str, Any]] = state["chunk_results"]
        for update in worker_updates:
            merged_results = _merge_chunk_results(merged_results, update.get("chunk_results", []))

        final_state = cast(
            VideoSummaryState,
            {
                "chunk_plan": chunk_plan,
                "chunk_results": merged_results,
                "user_prompt": "focus",
            },
        )

        result = chunk_synthesizer_node(final_state)
        self.assertEqual(len(result["chunk_results"]), 2)
        self.assertEqual(result["chunk_results"][0].get("chunk_summary"), "summary-c1")
        self.assertEqual(result["chunk_results"][1].get("chunk_summary"), "summary-c2")
        self.assertEqual(
            result["chunk_results"][0].get("_debug_context"),
            str(state.get("structured_global_context", "")),
        )


if __name__ == "__main__":
    unittest.main()
