"""
前端状态消息映射测试

验证 api.py 中的 node_msg_map 能够正确传递给 status_callback，
并包含关于 planchecker 和微智能体群的播报信息。
"""

import unittest
import json
from unittest.mock import MagicMock, patch
from core.workflow.api import analyze_video


class TestApiStatusMessages(unittest.TestCase):
    """验证 API 的前端状态透传"""

    def test_status_callback_receives_planchecker_message(self):
        """验证分片规划完成后的播报信息被正确传递（完成时态）"""
        messages = []

        def mock_callback(msg):
            messages.append(msg)

        # 模拟最小化的状态
        transcript = '{"segments": [{"id": 0, "start": 0, "end": 10, "text": "test"}]}'
        keyframes = [{"time": "00:00", "image": "base64_dummy"}]

        with patch("core.workflow.api.build_video_summary_graph") as mock_graph:
            # 创建一个 mock graph 应用
            mock_app = MagicMock()
            mock_graph.return_value = mock_app

            # 模拟工作流的流式输出，包括 chunk_planner_node 的输出
            mock_app.stream.return_value = iter([
                {
                    "chunk_planner_node": {
                        "chunk_plan": [
                            {
                                "chunk_id": "c1",
                                "start_sec": 0,
                                "end_sec": 120,
                                "transcript_segment_indexes": [0],
                                "keyframe_indexes": [0],
                            }
                        ]
                    }
                },
                {"map_dispatch_node": {}},
            ])

            # 调用 API
            try:
                analyze_video(
                    transcript=transcript,
                    keyframes=keyframes,
                    status_callback=mock_callback
                )
            except Exception:
                # 由于 mock，可能会失败，但我们只关心 callback 是否被调用
                pass

            # 验证分片规划完成的消息被传递（完成时态）
            plan_checker_msgs = [m for m in messages if isinstance(m, str) and "📋" in m and "分片规划完成" in m]
            self.assertTrue(
                len(plan_checker_msgs) > 0,
                "分片规划完成的播报信息应该被传递给 status_callback"
            )

            # 验证消息内容包含正确的信息
            if plan_checker_msgs:
                self.assertIn("分片", plan_checker_msgs[0])
                self.assertIn("开始并行分析", plan_checker_msgs[0])

    def test_status_callback_receives_multimodal_worker_messages(self):
        """验证多模态 worker (chunk_multimodal_worker_node) 通过 [[PROGRESS]] 事件播报进度"""
        messages = []

        def mock_callback(msg):
            messages.append(msg)

        transcript = '{"segments": [{"id": 0, "start": 0, "end": 10, "text": "test"}]}'
        keyframes = [{"time": "00:00", "image": "base64_dummy"}]

        with patch("core.workflow.api.build_video_summary_graph") as mock_graph:
            mock_app = MagicMock()
            mock_graph.return_value = mock_app

            # 模拟包含多模态 worker 和聚合节点的工作流输出
            mock_app.stream.return_value = iter([
                {"chunk_planner_node": {"chunk_plan": [{"chunk_id": "c1"}]}},
                {"map_dispatch_node": {}},
                {"chunk_multimodal_worker_node": {"chunk_results": [{"chunk_id": "c1", "chunk_insights_md": "test"}]}},
                {"chunk_aggregator_node": {"chunk_results": [{"chunk_id": "c1", "chunk_summary": "test"}]}},
            ])

            try:
                analyze_video(
                    transcript=transcript,
                    keyframes=keyframes,
                    status_callback=mock_callback
                )
            except Exception:
                pass

            # 验证多模态 worker 产生了结构化进度事件
            progress_msgs = [m for m in messages if isinstance(m, str) and m.startswith("[[PROGRESS]]")]
            self.assertTrue(len(progress_msgs) > 0, "多模态 worker 应该产生 [[PROGRESS]] 进度事件")

            # 验证 Chunk Aggregator 的消息（完成时态）
            agg_msgs = [m for m in messages if isinstance(m, str) and "🧾" in m and "分片洞察整合完成" in m]
            self.assertTrue(len(agg_msgs) > 0, "Chunk Aggregator 消息应该以完成时态传递")

    def test_dispatcher_message_present(self):
        """验证分发器 (Dispatcher) 的播报信息（完成时态）"""
        messages = []

        def mock_callback(msg):
            messages.append(msg)

        transcript = '{"segments": [{"id": 0, "start": 0, "end": 10, "text": "test"}]}'
        keyframes = [{"time": "00:00", "image": "base64_dummy"}]

        with patch("core.workflow.api.build_video_summary_graph") as mock_graph:
            mock_app = MagicMock()
            mock_graph.return_value = mock_app

            mock_app.stream.return_value = iter([
                {"map_dispatch_node": {}},
            ])

            try:
                analyze_video(
                    transcript=transcript,
                    keyframes=keyframes,
                    status_callback=mock_callback
                )
            except Exception:
                pass

            # 验证 Dispatcher 的消息（完成时态）
            dispatcher_msgs = [m for m in messages if isinstance(m, str) and "🗺️" in m and "分片执行配方" in m]
            self.assertTrue(
                len(dispatcher_msgs) > 0,
                "Dispatcher 的播报信息应该以完成时态传递给 status_callback"
            )

    def test_chunk_aggregator_shows_chunk_count(self):
        """验证 chunk_aggregator 完成时动态显示分片计数"""
        messages = []

        def mock_callback(msg):
            messages.append(msg)

        transcript = '{"segments": []}'
        keyframes = []

        with patch("core.workflow.api.build_video_summary_graph") as mock_graph:
            mock_app = MagicMock()
            mock_graph.return_value = mock_app

            # 模拟包含 3 个分片的完整结果
            mock_app.stream.return_value = iter([
                {
                    "chunk_aggregator_node": {
                        "chunk_results": [
                            {"chunk_id": "c1", "chunk_summary": "summary1"},
                            {"chunk_id": "c2", "chunk_summary": "summary2"},
                            {"chunk_id": "c3", "chunk_summary": "summary3"},
                        ]
                    }
                }
            ])

            try:
                analyze_video(
                    transcript=transcript,
                    keyframes=keyframes,
                    status_callback=mock_callback
                )
            except Exception:
                pass

            # 验证分片计数信息
            synth_msgs = [m for m in messages if isinstance(m, str) and "🧾" in m and "分片洞察整合完成" in m]
            self.assertTrue(
                any("3 个分片" in m for m in synth_msgs),
                "Chunk Aggregator 消息应该包含分片计数"
            )

    def test_human_gate_message_present(self):
        """验证第一阶段结束时会播报人类审批节点消息"""
        messages = []
        
        def mock_callback(msg):
            messages.append(msg)
        
        transcript = '{"segments": []}'
        keyframes = []
        
        with patch("core.workflow.api.build_video_summary_graph") as mock_graph:
            mock_app = MagicMock()
            mock_graph.return_value = mock_app
            
            # 模拟进入人类审批节点
            mock_app.stream.return_value = iter([
                {
                    "human_gate_node": {
                        "human_gate_status": "pending",
                        "human_edited_aggregated_insights": "draft",
                    }
                }
            ])
            
            try:
                analyze_video(
                    transcript=transcript,
                    keyframes=keyframes,
                    status_callback=mock_callback
                )
            except Exception:
                pass
            
            gate_msgs = [m for m in messages if "Human Gate" in m or "🧑‍⚖️" in m
]
            self.assertTrue(
                len(gate_msgs) > 0,
                "Human Gate 节点消息应该被透传"
            )

    def test_send_api_progress_event_tracks_done_count(self):
        """验证 send_api 进度事件使用 done_count 追踪分片完成数"""
        messages = []

        def mock_callback(msg):
            messages.append(msg)

        transcript = '{"segments": []}'
        keyframes = []

        with patch("core.workflow.api.build_video_summary_graph") as mock_graph:
            mock_app = MagicMock()
            mock_graph.return_value = mock_app

            mock_app.stream.return_value = iter([
                {"chunk_planner_node": {"chunk_plan": [{"chunk_id": "c1"}, {"chunk_id": "c2"}]}},
                {
                    "chunk_multimodal_worker_node": {
                        "chunk_results": [
                            {"chunk_id": "c1", "chunk_insights_md": "insights1"},
                        ]
                    }
                },
                {
                    "chunk_multimodal_worker_node": {
                        "chunk_results": [
                            {"chunk_id": "c2", "chunk_insights_md": "insights2"},
                        ]
                    }
                },
            ])

            analyze_video(
                transcript=transcript,
                keyframes=keyframes,
                status_callback=mock_callback,
            )

        progress_msgs = [m for m in messages if isinstance(m, str) and m.startswith("[[PROGRESS]]")]
        self.assertTrue(progress_msgs, "应至少产生一条结构化进度事件")

        payload = json.loads(progress_msgs[-1][len("[[PROGRESS]]"):])
        self.assertEqual(payload.get("type"), "chunk_progress")
        self.assertEqual(payload.get("total_chunks"), 2)
        self.assertEqual(payload.get("done_count"), 2)
        self.assertGreaterEqual(payload.get("overall_percent"), 0)


if __name__ == "__main__":
    unittest.main()

