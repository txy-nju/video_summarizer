import unittest
from unittest.mock import patch
from typing import cast

from core.workflow.video_summary.planner.chunk_planner import chunk_planner_node
from core.workflow.video_summary.state import VideoSummaryState


class TestChunkPlannerNode(unittest.TestCase):
    def test_builds_chunk_plan_from_transcript_and_keyframes(self):
        state = {
            "transcript": '{"segments": [{"start": 0, "end": 35, "text": "a"}, {"start": 40, "end": 140, "text": "b"}]}',
            "keyframes": [
                {"time": "00:10", "image": "x"},
                {"time": "01:10", "image": "y"},
                {"time": "02:10", "image": "z"},
            ],
        }

        with patch("core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 60):
            result = chunk_planner_node(state)  # type: ignore

        self.assertIn("video_duration_seconds", result)
        self.assertIn("chunk_plan", result)
        self.assertEqual(result["video_duration_seconds"], 140)
        self.assertTrue(result["chunk_plan"])
        self.assertEqual(len(result["chunk_plan"]), 3)

        first_chunk = result["chunk_plan"][0]
        self.assertEqual(first_chunk["chunk_id"], "chunk-000")
        self.assertGreaterEqual(first_chunk["end_sec"], first_chunk["start_sec"])
        self.assertIn("keyframe_indexes", first_chunk)
        self.assertIn("transcript_segment_indexes", first_chunk)
        self.assertEqual(first_chunk["start_sec"], 0)
        self.assertEqual(first_chunk["end_sec"], 60)
        self.assertEqual(first_chunk["keyframe_indexes"], [0])
        self.assertEqual(first_chunk["transcript_segment_indexes"], [0, 1])

        second_chunk = result["chunk_plan"][1]
        self.assertEqual(second_chunk["chunk_id"], "chunk-001")
        self.assertEqual(second_chunk["start_sec"], 60)
        self.assertEqual(second_chunk["end_sec"], 120)
        self.assertEqual(second_chunk["keyframe_indexes"], [1])
        self.assertEqual(second_chunk["transcript_segment_indexes"], [1])

        third_chunk = result["chunk_plan"][2]
        self.assertEqual(third_chunk["chunk_id"], "chunk-002")
        self.assertEqual(third_chunk["start_sec"], 120)
        self.assertEqual(third_chunk["end_sec"], 140)
        self.assertEqual(third_chunk["keyframe_indexes"], [2])
        self.assertEqual(third_chunk["transcript_segment_indexes"], [1])

    def test_fallback_for_empty_inputs(self):
        result = chunk_planner_node({"transcript": "", "keyframes": []})  # type: ignore
        self.assertEqual(result["video_duration_seconds"], 0)
        self.assertEqual(len(result["chunk_plan"]), 1)
        self.assertEqual(result["chunk_plan"][0]["start_sec"], 0)
        self.assertEqual(result["chunk_plan"][0]["end_sec"], 0)

    def test_handles_malformed_transcript_json(self):
        state = cast(
            VideoSummaryState,
            {
                "transcript": "{bad-json}",
                "keyframes": [{"time": "00:05", "image": "x"}],
            },
        )

        with patch("core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 60):
            result = chunk_planner_node(state)

        self.assertEqual(result["video_duration_seconds"], 5)
        self.assertEqual(len(result["chunk_plan"]), 1)
        self.assertEqual(result["chunk_plan"][0]["keyframe_indexes"], [0])
        self.assertEqual(result["chunk_plan"][0]["transcript_segment_indexes"], [])

    def test_ignores_keyframes_without_time(self):
        state = cast(
            VideoSummaryState,
            {
                "transcript": '{"segments": [{"start": 0, "end": 10, "text": "a"}]}',
                "keyframes": [
                    {"image": "missing-time"},
                    {"time": "00:08", "image": "ok"},
                ],
            },
        )

        with patch("core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 60):
            result = chunk_planner_node(state)

        self.assertEqual(result["video_duration_seconds"], 10)
        self.assertEqual(len(result["chunk_plan"]), 1)
        self.assertEqual(result["chunk_plan"][0]["keyframe_indexes"], [1])

    def test_uses_duration_field_when_segments_missing(self):
        state = cast(
            VideoSummaryState,
            {
                "transcript": '{"text": "hello", "language": "en", "duration": 95.4}',
                "keyframes": [],
            },
        )

        with patch("core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 60):
            result = chunk_planner_node(state)

        self.assertEqual(result["video_duration_seconds"], 95)
        self.assertEqual(len(result["chunk_plan"]), 2)
        self.assertEqual(result["chunk_plan"][0]["start_sec"], 0)
        self.assertEqual(result["chunk_plan"][0]["end_sec"], 60)
        self.assertEqual(result["chunk_plan"][1]["start_sec"], 60)
        self.assertEqual(result["chunk_plan"][1]["end_sec"], 95)

    def test_supports_chunks_timestamp_format(self):
        state = cast(
            VideoSummaryState,
            {
                "transcript": (
                    '{"chunks": ['
                    '{"timestamp": [0, 12], "text": "a"}, '
                    '{"timestamp": [65, 78], "text": "b"}'
                    '], "duration": 80}'
                ),
                "keyframes": [{"time": "00:05", "image": "x"}],
            },
        )

        with patch("core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 60):
            result = chunk_planner_node(state)

        self.assertEqual(result["video_duration_seconds"], 80)
        self.assertEqual(len(result["chunk_plan"]), 2)
        self.assertEqual(result["chunk_plan"][0]["transcript_segment_indexes"], [0])
        self.assertEqual(result["chunk_plan"][1]["transcript_segment_indexes"], [1])

    def test_planner_equivalence_between_single_and_merged_transcript(self):
        """单文件 verbose_json 与大文件合并后的 verbose_json 应得到一致的分片规划。"""
        keyframes = [
            {"time": "00:10", "image": "x"},
            {"time": "00:50", "image": "y"},
        ]

        single_transcript = (
            '{"task":"transcribe","language":"en","duration":70,'
            '"text":"first second",'
            '"segments":['
            '{"id":0,"start":0.0,"end":8.0,"text":"first"},'
            '{"id":1,"start":45.0,"end":55.0,"text":"second"}'
            ']}'
        )

        merged_transcript = (
            '{"task":"transcribe","language":"en","duration":70,'
            '"text":"first second",'
            '"segments":['
            '{"id":0,"start":0.0,"end":8.0,"text":"first"},'
            '{"id":1,"start":45.0,"end":55.0,"text":"second"}'
            ']}'
        )

        with patch("core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 60):
            single_result = chunk_planner_node({"transcript": single_transcript, "keyframes": keyframes})  # type: ignore
            merged_result = chunk_planner_node({"transcript": merged_transcript, "keyframes": keyframes})  # type: ignore

        self.assertEqual(single_result["video_duration_seconds"], merged_result["video_duration_seconds"])
        self.assertEqual(len(single_result["chunk_plan"]), len(merged_result["chunk_plan"]))

        for left, right in zip(single_result["chunk_plan"], merged_result["chunk_plan"]):
            self.assertEqual(left["chunk_id"], right["chunk_id"])
            self.assertEqual(left["start_sec"], right["start_sec"])
            self.assertEqual(left["end_sec"], right["end_sec"])
            self.assertEqual(left["keyframe_indexes"], right["keyframe_indexes"])
            self.assertEqual(left["transcript_segment_indexes"], right["transcript_segment_indexes"])

    def test_scene_anchor_alignment_moves_boundary_when_constraints_satisfied(self):
        state = cast(
            VideoSummaryState,
            {
                "transcript": '{"duration": 360}',
                "keyframes": [
                    {"time": "01:40", "image": "x", "scene_change_score": 0.9, "scene_change_level": "severe"},
                ],
            },
        )

        with patch("core.workflow.video_summary.planner.chunk_planner.ENABLE_SCENE_ANCHORED_CHUNK_SPLIT", True), patch(
            "core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 120
        ), patch("core.workflow.video_summary.planner.chunk_planner.CHUNK_SPLIT_ANCHOR_SEARCH_SECONDS", 60), patch(
            "core.workflow.video_summary.planner.chunk_planner.CHUNK_SPLIT_DURATION_SHORT_SECONDS", 600
        ), patch("core.workflow.video_summary.planner.chunk_planner.CHUNK_SPLIT_DURATION_MEDIUM_SECONDS", 1800), patch(
            "core.workflow.video_summary.planner.chunk_planner.CHUNK_SPLIT_MIN_DURATION_SHORT_SECONDS", 60
        ):
            result = chunk_planner_node(state)

        self.assertEqual(result["chunk_plan"][0]["end_sec"], 100)
        self.assertEqual(result["chunk_plan"][0]["split_anchor_source"], "scene")
        self.assertEqual(result["chunk_plan"][0]["split_anchor_time"], 100)

    def test_scene_anchor_rejected_when_min_duration_violated(self):
        state = cast(
            VideoSummaryState,
            {
                "transcript": '{"duration": 3600}',
                "keyframes": [
                    {"time": "07:40", "image": "x", "scene_change_score": 0.9, "scene_change_level": "severe"},
                ],
            },
        )

        with patch("core.workflow.video_summary.planner.chunk_planner.ENABLE_SCENE_ANCHORED_CHUNK_SPLIT", True), patch(
            "core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 480
        ), patch("core.workflow.video_summary.planner.chunk_planner.CHUNK_SPLIT_ANCHOR_SEARCH_SECONDS", 60), patch(
            "core.workflow.video_summary.planner.chunk_planner.CHUNK_SPLIT_DURATION_SHORT_SECONDS", 600
        ), patch("core.workflow.video_summary.planner.chunk_planner.CHUNK_SPLIT_DURATION_MEDIUM_SECONDS", 1800), patch(
            "core.workflow.video_summary.planner.chunk_planner.CHUNK_SPLIT_MIN_DURATION_LONG_SECONDS", 600
        ):
            result = chunk_planner_node(state)

        self.assertEqual(result["chunk_plan"][0]["end_sec"], 450)
        self.assertEqual(result["chunk_plan"][0]["split_anchor_source"], "fixed")
        self.assertIsNone(result["chunk_plan"][0]["split_anchor_time"])

    def test_scene_anchor_toggle_off_keeps_fixed_windows(self):
        state = cast(
            VideoSummaryState,
            {
                "transcript": '{"duration": 360}',
                "keyframes": [
                    {"time": "01:40", "image": "x", "scene_change_score": 0.9, "scene_change_level": "severe"},
                ],
            },
        )

        with patch("core.workflow.video_summary.planner.chunk_planner.ENABLE_SCENE_ANCHORED_CHUNK_SPLIT", False), patch(
            "core.workflow.video_summary.planner.chunk_planner.MAP_CHUNK_SECONDS", 120
        ):
            result = chunk_planner_node(state)

        self.assertEqual(result["chunk_plan"][0]["end_sec"], 120)
        self.assertEqual(result["chunk_plan"][0]["split_anchor_source"], "fixed")
        self.assertIsNone(result["chunk_plan"][0]["split_anchor_time"])


if __name__ == "__main__":
    unittest.main()
