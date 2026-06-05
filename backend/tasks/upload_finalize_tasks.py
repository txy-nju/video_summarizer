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

from backend.tasks.base_task import BaseTask
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _create_upload_service():
    from backend.repositories.upload_repository import UploadRepository
    from backend.services.upload_service import UploadService

    import redis as redis_lib

    import os
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    return UploadService(UploadRepository(redis_client))


@celery_app.task(
    bind=True,
    name="backend.tasks.upload_finalize_tasks.async_finalize_upload",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
    task_soft_time_limit=600,
    task_time_limit=900,
)
def async_finalize_upload(self, upload_id: str, trace_id: str = "") -> dict:
    """
    异步完成上传最终化：
    - 合并分片
    - 写入 video_resource.oss_key
    - 发布 VideoUploadedEvent → 触发 async_process_video

    幂等性：若上传已处理（UploadService 状态为 done），直接返回。
    重试安全：_create_video_resource 会复用预注册记录，OSS 上传覆盖写，
    consumer 侧通过 DB 状态判断跳过重复处理。
    """
    service = _create_upload_service()
    try:
        result = service.finalize_upload(upload_id=upload_id)

        if result.get("status") == "MERGED":
            owner_id = result.get("owner_id", "")
            file_name = result.get("file_name", "")
            merged_path = result.get("merged_path", "")

            # Step 1: 创建或复用 VideoResource 记录（幂等）
            video_id = _create_video_resource(
                owner_id=owner_id,
                file_name=file_name,
                video_id=result.get("video_id"),
            )
            if video_id is None:
                logger.error("async_finalize_upload: failed to create video_resource for upload_id=%s", upload_id)
                return {"upload_id": upload_id, "status": "FAILED", "error": "Failed to create video_resource"}

            # Step 2: 上传合并文件到对象存储（覆盖写，幂等）
            object_key = _build_video_object_key(
                owner_id=owner_id, video_id=video_id, file_name=file_name, merged_path=merged_path
            )
            from backend.infrastructure.storage.oss_client import get_object_storage_client

            storage_client = get_object_storage_client()
            stored_key = storage_client.upload_file(local_path=Path(merged_path), object_key=object_key)
            _set_video_resource_oss_key(video_id=video_id, oss_key=stored_key)

            # Step 3: 清理分片文件
            from backend.repositories.upload_repository import UploadRepository
            import redis as redis_lib

            import os
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
            redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
            UploadRepository(redis_client).cleanup_chunks(upload_id)
            UploadRepository(redis_client).update_state(upload_id, "done")

            # Step 4: 发布 VideoUploadedEvent
            _publish_video_uploaded_event(
                video_id=video_id, owner_id=owner_id, oss_key=stored_key, trace_id=trace_id
            )

            logger.info(
                "async_finalize_upload completed: upload_id=%s, video_id=%s, oss_key=%s, trace_id=%s",
                upload_id, video_id, stored_key, trace_id,
            )
            return {
                "upload_id": upload_id,
                "video_id": video_id,
                "status": "DONE",
                "oss_key": stored_key,
                "trace_id": trace_id,
            }

        return result

    except Exception as exc:
        logger.exception("async_finalize_upload failed for upload_id=%s trace_id=%s", upload_id, trace_id)
        # 重试耗尽时记录 CRITICAL 日志并持久化死信
        if self.is_last_attempt:
            logger.critical(
                "async_finalize_upload: retries exhausted for upload_id=%s trace_id=%s",
                upload_id, trace_id,
            )
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())


def _build_video_object_key(*, owner_id: str, video_id: str, file_name: str, merged_path: str) -> str:
    suffix = Path(file_name).suffix or Path(merged_path).suffix or ".mp4"
    return f"videos/{owner_id}/{video_id}/original{suffix.lower()}"


def _create_video_resource(
    *,
    owner_id: str,
    file_name: str,
    video_id: str | None = None,
) -> str | None:
    """创建或复用 VideoResource 记录（系统内部操作）。

    优先路径：若提供了显式 video_id，通过 video_id + owner_id 精确查找
             目标记录，oss_key 为空时直接复用。
    后备路径：video_id 未提供、记录不存在或已被占用时，回退到
             (owner_id, file_name, 空 oss_key) 的文件名模糊匹配。
    """
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource
    from backend.schemas.video_resource import VideoResourceCreateRequest
    from backend.repositories.video_resource_repository import VideoResourceRepository
    from backend.services.video_resource_service import VideoResourceService

    db = SessionLocal()
    try:
        # ── 优先路径：显式 video_id → 精确查找 ──
        if video_id:
            row = (
                db.query(VideoResource)
                .filter(
                    VideoResource.video_id == video_id,
                    VideoResource.owner_id == owner_id,
                )
                .one_or_none()
            )
            if row is not None and _oss_key_is_empty(row.oss_key):
                logger.info(
                    "Reusing explicitly-provided VideoResource: video_id=%s, file_name=%s",
                    video_id, file_name,
                )
                return str(row.video_id)
            if row is not None and not _oss_key_is_empty(row.oss_key):
                logger.warning(
                    "Explicit video_id=%s already has oss_key set, falling back to file_name match",
                    video_id,
                )
            else:
                logger.warning(
                    "Explicit video_id=%s not found for owner_id=%s, falling back to file_name match",
                    video_id, owner_id,
                )

        # ── 后备路径（向后兼容）：文件名模糊匹配 ──
        row = (
            db.query(VideoResource)
            .filter(
                VideoResource.owner_id == owner_id,
                VideoResource.file_name == file_name,
                (VideoResource.oss_key == "") | (VideoResource.oss_key.is_(None)),
            )
            .order_by(VideoResource.video_id.desc())
            .first()
        )
        if row is not None:
            logger.info("Found pre-registered VideoResource by file_name: video_id=%s, reusing it.", row.video_id)
            return str(row.video_id)

        # 2. 如果没有预注册的同名记录，才新建一条记录
        repo = VideoResourceRepository(db_session=db)
        service = VideoResourceService(repository=repo)

        view = service.create_video_resource(
            owner_id=owner_id,
            payload=VideoResourceCreateRequest(file_name=file_name),
        )
        return view.video_id
    finally:
        db.close()


def _oss_key_is_empty(oss_key: str | None) -> bool:
    return not oss_key or oss_key.strip() == ""


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


def _publish_video_uploaded_event(*, video_id: str, owner_id: str, oss_key: str, trace_id: str = "") -> None:
    """通过领域事件总线（Redis Streams）发布 VideoUploadedEvent。

    发布方不感知消费方：只发 XADD，不知道谁会 XREADGROUP。
    消费方 domain_event_listener 独立监听并触发 async_process_video。
    """
    try:
        import redis as redis_lib

        from backend.schemas.domain_event import DomainEvent
        from backend.services.domain_event_bus import DomainEventBus

        import os
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
        redis_client = redis_lib.Redis.from_url(
            redis_url, decode_responses=True
        )
        bus = DomainEventBus(redis_client)

        event = DomainEvent(
            event_type="video_uploaded",
            scope="video_resource",
            scope_id=video_id,
            trace_id=trace_id,
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
