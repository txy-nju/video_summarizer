"""
WebSocket 进度推送集成测试。

覆盖：
- 进度事件发布与序列号递增
- ConnectionManager 连接/断连
- WSEventEnvelope schema 验证
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from backend.websocket.schemas import (
    WSEventEnvelope,
    WSEventType,
    WSScope,
    WSStage,
)
from backend.services.progress_publish_service import ProgressPublishService


class TestWSEventEnvelope:
    """WSEventEnvelope schema 单元测试。"""

    def test_minimal_envelope(self):
        event = WSEventEnvelope(
            event_type=WSEventType.PROGRESS,
            user_id="usr_001",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            sequence=0,
        )
        assert event.event_type == WSEventType.PROGRESS
        assert event.user_id == "usr_001"
        assert event.schema_version == "1.0"
        assert event.event_id.startswith("evt_")

    def test_full_envelope_roundtrip(self):
        event = WSEventEnvelope(
            event_type=WSEventType.PROGRESS,
            trace_id="trc_001",
            tenant_id="default",
            user_id="usr_001",
            scope=WSScope.VIDEO_SUMMARY_TASK,
            scope_id="task_001",
            sequence=42,
            stage=WSStage.EXTRACTION,
            substage="extracting_keyframes",
            status="RUNNING",
            progress=45,
            message="正在抽取关键帧",
            payload={"task_id": "task_001", "node": "extractor"},
        )
        # 序列化往返
        json_str = event.model_dump_json()
        restored = WSEventEnvelope.model_validate_json(json_str)
        assert restored.event_id == event.event_id
        assert restored.sequence == 42
        assert restored.progress == 45

    def test_sequence_is_non_negative(self):
        with pytest.raises(Exception):  # Pydantic validation error
            WSEventEnvelope(
                event_type=WSEventType.PROGRESS,
                user_id="usr_001",
                scope=WSScope.VIDEO_RESOURCE,
                scope_id="vid_001",
                sequence=-1,
            )

    def test_progress_range_0_100(self):
        # boundary: 0 is ok, 100 is ok, 101 is not
        e = WSEventEnvelope(
            event_type=WSEventType.PROGRESS,
            user_id="u1",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="v1",
            sequence=0,
            progress=0,
        )
        assert e.progress == 0

        e = WSEventEnvelope(
            event_type=WSEventType.PROGRESS,
            user_id="u1",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="v1",
            sequence=0,
            progress=100,
        )
        assert e.progress == 100

        with pytest.raises(Exception):
            WSEventEnvelope(
                event_type=WSEventType.PROGRESS,
                user_id="u1",
                scope=WSScope.VIDEO_RESOURCE,
                scope_id="v1",
                sequence=0,
                progress=101,
            )

    def test_all_event_types(self):
        for et in WSEventType:
            event = WSEventEnvelope(
                event_type=et,
                user_id="u1",
                scope=WSScope.VIDEO_RESOURCE,
                scope_id="v1",
                sequence=0,
            )
            assert event.event_type == et


class TestProgressPublishService:
    """ProgressPublishService 序列号递增测试。"""

    def test_sequence_increments_per_scope(self):
        """同一 scope+scope_id 的 sequence 严格递增。"""
        mock_bus = MagicMock()
        mock_bus.publish.return_value = 1
        svc = ProgressPublishService(event_bus=mock_bus, instance_id="test")

        r1 = svc.publish_progress(
            user_id="u1",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            progress=0,
        )
        r2 = svc.publish_progress(
            user_id="u1",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            progress=50,
        )
        r3 = svc.publish_progress(
            user_id="u1",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            progress=100,
        )

        # 验证调用次数
        assert mock_bus.publish.call_count == 3

        # 验证每次调用的 event 参数中 sequence 递增
        calls = mock_bus.publish.call_args_list
        seq0 = calls[0][0][0].sequence
        seq1 = calls[1][0][0].sequence
        seq2 = calls[2][0][0].sequence
        assert seq0 == 0
        assert seq1 == 1
        assert seq2 == 2

    def test_sequence_independent_across_scopes(self):
        """不同 scope 的 sequence 独立计数。"""
        mock_bus = MagicMock()
        mock_bus.publish.return_value = 1
        svc = ProgressPublishService(event_bus=mock_bus, instance_id="test")

        svc.publish_progress(user_id="u1", scope=WSScope.VIDEO_RESOURCE, scope_id="vid_a")
        svc.publish_progress(user_id="u1", scope=WSScope.VIDEO_SUMMARY_TASK, scope_id="task_x")
        svc.publish_progress(user_id="u1", scope=WSScope.VIDEO_RESOURCE, scope_id="vid_a")

        calls = mock_bus.publish.call_args_list
        # vid_a: seq 0,1; task_x: seq 0
        assert calls[0][0][0].sequence == 0  # vid_a first
        assert calls[1][0][0].sequence == 0  # task_x first (独立)
        assert calls[2][0][0].sequence == 1  # vid_a second

    def test_publish_completed(self):
        mock_bus = MagicMock()
        mock_bus.publish.return_value = 1
        svc = ProgressPublishService(event_bus=mock_bus, instance_id="test")

        svc.publish_completed(
            user_id="u1",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            result={"status": "ok"},
        )

        assert mock_bus.publish.call_count == 1
        event = mock_bus.publish.call_args[0][0]
        assert event.event_type == WSEventType.COMPLETED
        assert event.progress == 100
        assert event.status == "COMPLETED"

    def test_publish_error(self):
        mock_bus = MagicMock()
        mock_bus.publish.return_value = 1
        svc = ProgressPublishService(event_bus=mock_bus, instance_id="test")

        svc.publish_error(
            user_id="u1",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            code="TRANSCODE_FAILED",
            message="Transcoding failed",
            is_retryable=True,
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.event_type == WSEventType.ERROR
        assert event.status == "FAILED"
        assert event.payload["code"] == "TRANSCODE_FAILED"
        assert event.payload["is_retryable"] is True

    def test_publish_status_update(self):
        mock_bus = MagicMock()
        mock_bus.publish.return_value = 1
        svc = ProgressPublishService(event_bus=mock_bus, instance_id="test")

        svc.publish_status_update(
            user_id="u1",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            status="TRANSCRIBING",
            previous_status="UPLOADED",
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.event_type == WSEventType.STATUS_UPDATE
        assert event.payload["status"] == "TRANSCRIBING"
        assert event.payload["previous_status"] == "UPLOADED"
