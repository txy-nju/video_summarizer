"""
领域事件消费者（独立 Celery Worker）。

职责：
- 作为独立 Worker 进程运行（与上传 Worker 无进程耦合）
- 监听 Redis Streams 中的领域事件
- 按事件类型路由到对应的 Service 处理方法

当前支持的事件类型：
- video_uploaded → VideoResourceService.trigger_processing_after_upload()

启动方式（Celery）：
    celery -A backend.tasks.celery_app worker \
        -Q domain_events \
        -n domain_event_worker@%h \
        --concurrency=1
"""

from __future__ import annotations

import logging
import time

from backend.schemas.domain_event import DomainEvent

logger = logging.getLogger(__name__)

# 消费方标识
_CONSUMER_GROUP = "video-processing-workers"
_CONSUMER_NAME_BASE = "domain-event-listener"


def _build_consumer_name() -> str:
    import socket

    host = socket.gethostname()
    pid = __import__("os").getpid()
    return f"{_CONSUMER_NAME_BASE}-{host}-{pid}"


def run_domain_event_listener() -> None:
    """主循环：阻塞式消费领域事件。

    此函数设计为在独立 Celery Worker 进程中运行，
    通过 Celery 的 @worker_ready 信号或独立启动脚本触发。
    """
    import redis as redis_lib

    from backend.services.domain_event_bus import DomainEventBus

    redis_client = redis_lib.Redis.from_url(
        "redis://localhost:6379/2", decode_responses=True
    )
    bus = DomainEventBus(redis_client)
    consumer_name = _build_consumer_name()

    logger.info(
        "Domain event listener started: group=%s, consumer=%s",
        _CONSUMER_GROUP,
        consumer_name,
    )

    for event in bus.consume(
        consumer_group=_CONSUMER_GROUP,
        consumer_name=consumer_name,
        event_types=["video_uploaded"],
    ):
        _handle_event(event)


def _handle_event(event: DomainEvent) -> None:
    """按事件类型路由到对应的 Service 方法。"""
    logger.info(
        "Handling domain event: event_type=%s, event_id=%s, scope=%s, scope_id=%s",
        event.event_type,
        event.event_id,
        event.scope,
        event.scope_id,
    )

    handlers = {
        "video_uploaded": _handle_video_uploaded,
    }

    handler = handlers.get(event.event_type)
    if handler is None:
        logger.warning("No handler for event_type=%s", event.event_type)
        return

    try:
        handler(event)
    except Exception:
        logger.exception(
            "Failed to handle domain event: event_type=%s, event_id=%s",
            event.event_type,
            event.event_id,
        )


def _handle_video_uploaded(event: DomainEvent) -> None:
    """处理 video_uploaded 事件：触发内容加工域 async_process_video。"""
    video_id = event.payload.get("video_id", "")
    if not video_id:
        logger.warning("video_uploaded event missing video_id: event_id=%s", event.event_id)
        return

    from backend.db.session import SessionLocal
    from backend.repositories.video_resource_repository import VideoResourceRepository
    from backend.services.video_resource_service import VideoResourceService

    db = SessionLocal()
    try:
        repo = VideoResourceRepository(db_session=db)
        service = VideoResourceService(repository=repo)
        triggered = service.trigger_processing_after_upload(video_id=video_id)
        logger.info(
            "VideoUploadedEvent processed: video_id=%s, triggered=%s",
            video_id,
            triggered,
        )
    finally:
        db.close()
