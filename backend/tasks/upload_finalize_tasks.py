"""
上传最终化 Celery 任务。

职责（严格限定）：
1. 调用 UploadService 合并分片。
2. 创建 VideoResource 记录并写入 oss_key。
3. 发布 VideoUploadedEvent → 由 VideoResourceService.trigger_processing_after_upload 触发 async_process_video。

约束：
- 不得直接调用 async_process_video（解耦上传域与内容加工域）。
- oss_key 在 OSS 集成前暂存本地文件路径；后续替换为真实 OSS 对象键。
"""

from __future__ import annotations

import logging

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
        oss_key = result.get("merged_path", "")

        # Step 1: 创建 VideoResource 记录并写入 oss_key
        video_id = _create_video_resource_and_set_oss_key(
            owner_id=owner_id,
            file_name=file_name,
            oss_key=oss_key,
        )
        if video_id is None:
            logger.error("async_finalize_upload: failed to create video_resource for upload_id=%s", upload_id)
            return {"upload_id": upload_id, "status": "FAILED", "error": "Failed to create video_resource"}

        # Step 2: 清理分片文件（保留 merged 文件）
        from backend.repositories.upload_repository import UploadRepository
        import redis as redis_lib

        redis_client = redis_lib.Redis.from_url("redis://localhost:6379/2", decode_responses=True)
        UploadRepository(redis_client).cleanup_chunks(upload_id)
        UploadRepository(redis_client).update_state(upload_id, "done")

        # Step 3: 发布 VideoUploadedEvent → 内容加工域触发处理
        _trigger_video_processing(video_id)

        logger.info(
            "async_finalize_upload completed: upload_id=%s, video_id=%s, oss_key=%s",
            upload_id,
            video_id,
            oss_key,
        )
        return {
            "upload_id": upload_id,
            "video_id": video_id,
            "status": "DONE",
            "oss_key": oss_key,
        }

    return result


def _create_video_resource_and_set_oss_key(
    *,
    owner_id: str,
    file_name: str,
    oss_key: str,
) -> str | None:
    """创建 VideoResource 记录并写入 oss_key（系统内部操作）。"""
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource
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
        video_id = view.video_id

        # 写入 oss_key（system-only：直接通过 ORM 更新，避免绕过 service 的 owner 校验）
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        if row is not None:
            row.oss_key = oss_key
            db.commit()

        return video_id
    finally:
        db.close()


def _trigger_video_processing(video_id: str) -> None:
    """发布 VideoUploadedEvent，由内容加工域服务层监听并触发 async_process_video。"""
    from backend.db.session import SessionLocal
    from backend.repositories.video_resource_repository import VideoResourceRepository
    from backend.services.video_resource_service import VideoResourceService

    db = SessionLocal()
    try:
        repo = VideoResourceRepository(db_session=db)
        service = VideoResourceService(repository=repo)
        triggered = service.trigger_processing_after_upload(video_id=video_id)
        logger.info(
            "VideoUploadedEvent dispatched for video_id=%s, triggered=%s",
            video_id,
            triggered,
        )
    finally:
        db.close()
