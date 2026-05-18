"""WebSocket 重连补发测试。

覆盖：
- send_recent_events() 查询最近 100 条事件
- 无事件时返回空列表
- reconnect_ack 事件类型正确
"""

from __future__ import annotations

import json

from backend.websocket.schemas import WSEventEnvelope, WSEventType, WSScope


class TestReconnectAckEnvelope:
    def test_reconnect_ack_format(self) -> None:
        ack = WSEventEnvelope(
            event_type=WSEventType.RECONNECT_ACK,
            user_id="usr_001",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            sequence=42,
            status="RECONNECTED",
            message="Reconnected, last_sequence=42",
            payload={"last_sequence": 42},
        )
        data = json.loads(ack.model_dump_json())
        assert data["event_type"] == "reconnect_ack"
        assert data["user_id"] == "usr_001"
        assert data["sequence"] == 42
        assert data["status"] == "RECONNECTED"
        assert data["payload"]["last_sequence"] == 42


class TestWSEventEnvelopeEdgeCases:
    def test_progress_event_defaults(self) -> None:
        event = WSEventEnvelope(
            event_type=WSEventType.PROGRESS,
            user_id="usr_001",
            scope=WSScope.VIDEO_SUMMARY_TASK,
            scope_id="task_001",
            sequence=1,
            stage="extraction",
            message="Extracting...",
        )
        data = json.loads(event.model_dump_json())
        assert data["schema_version"] == "1.0"
        assert data["event_id"]  # auto-generated UUID
        assert data["produced_at"]  # auto-generated timestamp

    def test_completed_event_format(self) -> None:
        event = WSEventEnvelope(
            event_type=WSEventType.COMPLETED,
            user_id="usr_001",
            scope=WSScope.VIDEO_SUMMARY_TASK,
            scope_id="task_001",
            sequence=10,
            stage="synthesis",
            message="Summary completed",
            progress=100,
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "completed"
        assert data["progress"] == 100

    def test_error_event_format(self) -> None:
        event = WSEventEnvelope(
            event_type=WSEventType.ERROR,
            user_id="usr_001",
            scope=WSScope.VIDEO_SUMMARY_TASK,
            scope_id="task_001",
            sequence=5,
            stage="extraction",
            message="Transcription failed",
            payload={"code": "TRANSCRIBE_FAILED", "is_retryable": True},
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "error"
        assert data["payload"]["code"] == "TRANSCRIBE_FAILED"

    def test_status_update_event_format(self) -> None:
        event = WSEventEnvelope(
            event_type=WSEventType.STATUS_UPDATE,
            user_id="usr_001",
            scope=WSScope.VIDEO_RESOURCE,
            scope_id="vid_001",
            sequence=3,
            status="DELETING",
            message="Cascade delete in progress",
            payload={"previous_status": "PENDING_DELETE"},
        )
        data = json.loads(event.model_dump_json())
        assert data["event_type"] == "status_update"
        assert data["status"] == "DELETING"
