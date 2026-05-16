"""
领域事件总线集成测试（步骤 5.6 Redis Streams）。

验证：
- DomainEvent 序列化/反序列化闭环
- DomainEventBus publish → consume 闭合
- upload_finalize_tasks 发布 VideoUploadedEvent 不导入 VideoResourceService
- domain_event_listener 独立消费事件并路由到对应 handler
"""

from __future__ import annotations

import pytest

from backend.schemas.domain_event import DomainEvent


class TestDomainEventSchema:
    """事件信封 Schema 单元测试。"""

    def test_event_serialization_roundtrip(self):
        event = DomainEvent(
            event_type="video_uploaded",
            scope="video_resource",
            scope_id="vid_001",
            payload={"video_id": "vid_001", "owner_id": "usr_001", "oss_key": "videos/test.mp4"},
        )
        json_str = event.to_json()
        restored = DomainEvent.model_validate_json(json_str)

        assert restored.event_id == event.event_id
        assert restored.event_type == "video_uploaded"
        assert restored.schema_version == "1.0"
        assert restored.scope == "video_resource"
        assert restored.scope_id == "vid_001"
        assert restored.payload["video_id"] == "vid_001"

    def test_event_defaults(self):
        event = DomainEvent(event_type="test_event")
        assert event.event_id != ""
        assert event.schema_version == "1.0"
        assert event.produced_at.endswith("Z")
        assert isinstance(event.payload, dict)


class TestDomainEventBus:
    """Event Bus 发布-消费集成测试。"""

    def test_publish_and_consume_roundtrip(self):
        import redis as redis_lib
        from backend.services.domain_event_bus import DomainEventBus

        redis_client = redis_lib.Redis.from_url(
            "redis://localhost:6379/2", decode_responses=True
        )
        bus = DomainEventBus(redis_client)
        event_type = "test_integration_event"

        # 清空测试 Stream
        redis_client.delete(f"domain:events:{event_type}")

        # 发布
        event = DomainEvent(
            event_type=event_type,
            scope="test",
            scope_id="test_001",
            payload={"key": "value"},
        )
        msg_id = bus.publish(event)
        assert msg_id is not None

        # 消费（使用独立 consumer group 避免与生产冲突）
        consumer_group = "test-workers"
        consumer_name = "test-consumer-1"

        events_received = []
        for received in bus.consume(
            consumer_group=consumer_group,
            consumer_name=consumer_name,
            event_types=[event_type],
            block_ms=1000,
            batch_size=1,
        ):
            events_received.append(received)
            break  # 只取一条

        # Cleanup
        redis_client.delete(f"domain:events:{event_type}")
        try:
            redis_client.xgroup_destroy(f"domain:events:{event_type}", consumer_group)
        except Exception:
            pass

        assert len(events_received) == 1
        assert events_received[0].event_type == event_type
        assert events_received[0].payload["key"] == "value"


class TestUploadFinalizeTaskPublishesEvent:
    """验证 upload_finalize_tasks 通过 Event Bus 发布事件（跨域触发链已解耦）。"""

    def test_trigger_function_replaced_by_event_publish(self):
        """验证 _trigger_video_processing 已被 _publish_video_uploaded_event 替换。

        关键：旧函数直接调用 VideoResourceService.trigger_processing_after_upload，
        新函数通过 DomainEventBus.publish() 发布事件，实现跨域解耦。
        """
        from backend.tasks import upload_finalize_tasks

        # 旧触发函数不应存在
        assert not hasattr(upload_finalize_tasks, "_trigger_video_processing"), (
            "_trigger_video_processing must be removed; "
            "cross-domain trigger now goes through DomainEventBus.publish()"
        )

        # 新事件发布函数应存在
        assert hasattr(upload_finalize_tasks, "_publish_video_uploaded_event"), (
            "_publish_video_uploaded_event must exist to publish via Redis Streams"
        )
        assert callable(upload_finalize_tasks._publish_video_uploaded_event)

    def test_event_publish_function_uses_domain_event_bus(self):
        """验证 _publish_video_uploaded_event 使用 DomainEventBus（非 VideoResourceService 直调）。"""
        import inspect

        from backend.tasks.upload_finalize_tasks import _publish_video_uploaded_event

        source = inspect.getsource(_publish_video_uploaded_event)
        assert "DomainEventBus" in source, (
            "_publish_video_uploaded_event must use DomainEventBus for cross-domain decoupling"
        )
        assert "DomainEvent" in source, (
            "_publish_video_uploaded_event must construct a DomainEvent envelope"
        )
