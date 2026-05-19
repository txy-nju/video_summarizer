"""
上传最终化 Celery 任务。

职责（严格限定）：
1. 调用 UploadService 合并分片。
2. 创建 VideoResource 记录并写入 oss_key。
3. 通过领域事件总线（Redis Streams）发布 VideoUploadedEvent，
   由 domain_event_listener 独立消费并触发 async_process_video。

约束（已对齐步骤 5.6 边界）：
- upload_finalize_tasks 不 import VideoResourceService，不直接调用 async_process_video。
- 跨域协作仅通过 DomainEventBus.publish() → Redis XADD 发布事件。
- 消费方 domain_event_listener 独立生命周期，发布方不感知。
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _create_upload_service():
    from backend.repositories.upload_repository import UploadRepository
    from backend.services.upload_service import UploadService

    import redis as redis_lib

    redis_client = redis_lib.Redis.from_url("redis://localhost:6379/2", decode_responses=True)
    return UploadService(UploadRepository(redis_client))


@celery_app.task(
    name="backend.tasks.upload_finalize_tasks.async_finalize_upload",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def async_finalize_upload(upload_id: str) -> dict:
    """
    异步完成上传最终化：
    - 合并分片
    - 写入 video_resource.oss_key
    - 发布 VideoUploadedEvent → 触发 async_process_video
    """
    service = _create_upload_service()
    result = service.finalize_upload(upload_id=upload_id)

    if result.get("status") == "MERGED":
        owner_id = result.get("owner_id", "")
        file_name = result.get("file_name", "")
        merged_path = result.get("merged_path", "")

        # Step 1: 创建 VideoResource 记录
        video_id = _create_video_resource(
            owner_id=owner_id,
            file_name=file_name,
        )
        if video_id is None:
            logger.error("async_finalize_upload: failed to create video_resource for upload_id=%s", upload_id)
            return {"upload_id": upload_id, "status": "FAILED", "error": "Failed to create video_resource"}

        # Step 2: 上传合并文件到对象存储，并写入 video_resource.oss_key（对象键）
        object_key = _build_video_object_key(owner_id=owner_id, video_id=video_id, file_name=file_name, merged_path=merged_path)
        from backend.infrastructure.storage.oss_client import get_object_storage_client

        storage_client = get_object_storage_client()
        stored_key = storage_client.upload_file(local_path=Path(merged_path), object_key=object_key)
        _set_video_resource_oss_key(video_id=video_id, oss_key=stored_key)

        # Step 3: 清理分片文件（保留对象存储中的最终文件）
        from backend.repositories.upload_repository import UploadRepository
        import redis as redis_lib

        redis_client = redis_lib.Redis.from_url("redis://localhost:6379/2", decode_responses=True)
        UploadRepository(redis_client).cleanup_chunks(upload_id)
        UploadRepository(redis_client).update_state(upload_id, "done")

        # Step 4: 发布 VideoUploadedEvent → 领域事件总线（Redis Streams）
        #         上传域不感知消费方；domain_event_listener 独立消费并触发 async_process_video
        _publish_video_uploaded_event(video_id=video_id, owner_id=owner_id, oss_key=stored_key)

        logger.info(
            "async_finalize_upload completed: upload_id=%s, video_id=%s, oss_key=%s",
            upload_id,
            video_id,
            stored_key,
        )
        return {
            "upload_id": upload_id,
            "video_id": video_id,
            "status": "DONE",
            "oss_key": stored_key,
        }

    return result


def _build_video_object_key(*, owner_id: str, video_id: str, file_name: str, merged_path: str) -> str:
    suffix = Path(file_name).suffix or Path(merged_path).suffix or ".mp4"
    return f"videos/{owner_id}/{video_id}/original{suffix.lower()}"


def _create_video_resource(
    *,
    owner_id: str,
    file_name: str,
) -> str | None:
    """创建 VideoResource 记录（系统内部操作）。"""
    from backend.db.session import SessionLocal
    from backend.schemas.video_resource import VideoResourceCreateRequest
    from backend.repositories.video_resource_repository import VideoResourceRepository
    from backend.services.video_resource_service import VideoResourceService

    db = SessionLocal()
    try:
        repo = VideoResourceRepository(db_session=db)
        service = VideoResourceService(repository=repo)

        view = service.create_video_resource(
            owner_id=owner_id,
            payload=VideoResourceCreateRequest(file_name=file_name),
        )
        return view.video_id
    finally:
        db.close()


def _set_video_resource_oss_key(*, video_id: str, oss_key: str) -> None:
    """写入 video_resource.oss_key（系统内部操作）。"""
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource

    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        if row is None:
            return
        row.oss_key = oss_key
        db.commit()
    finally:
        db.close()


def _publish_video_uploaded_event(*, video_id: str, owner_id: str, oss_key: str) -> None:
    """通过领域事件总线（Redis Streams）发布 VideoUploadedEvent。

    发布方不感知消费方：只发 XADD，不知道谁会 XREADGROUP。
    消费方 domain_event_listener 独立监听并触发 async_process_video。
    """
    try:
        import redis as redis_lib

        from backend.schemas.domain_event import DomainEvent
        from backend.services.domain_event_bus import DomainEventBus

        redis_client = redis_lib.Redis.from_url(
            "redis://localhost:6379/2", decode_responses=True
        )
        bus = DomainEventBus(redis_client)

        event = DomainEvent(
            event_type="video_uploaded",
            scope="video_resource",
            scope_id=video_id,
            payload={
                "video_id": video_id,
                "owner_id": owner_id,
                "oss_key": oss_key,
            },
        )
        msg_id = bus.publish(event)
        logger.info(
            "VideoUploadedEvent published: event_id=%s, video_id=%s, stream_msg_id=%s",
            event.event_id,
            video_id,
            msg_id,
        )
    except Exception:
        logger.exception("Failed to publish VideoUploadedEvent for video_id=%s", video_id)
