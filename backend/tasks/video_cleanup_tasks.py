"""
视频资源跨存储级联清理任务。

async_cascade_delete_video 负责：
1. OSS 原始视频与关键帧目录清理（step 5.5 后接入真实 OSS client）
2. 向量库 transcript_vector_ids 对应切片删除（步骤 7 后接入）
3. 数据库物理删除

状态流转（幂等）：PENDING_DELETE -> DELETING -> PURGED / DELETE_FAILED
"""

from __future__ import annotations

import logging

from backend.infrastructure.storage.oss_client import get_object_storage_client
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _create_video_resource_service():
    """工厂：为每次任务调用创建独立 DB session + service 实例。"""
    from backend.db.session import SessionLocal
    from backend.repositories.video_resource_repository import VideoResourceRepository
    from backend.services.video_resource_service import VideoResourceService

    db = SessionLocal()
    return VideoResourceService(VideoResourceRepository(db)), db


@celery_app.task(
    bind=True,
    name="backend.tasks.video_cleanup_tasks.async_cascade_delete_video",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def async_cascade_delete_video(self, video_id: str) -> dict:
    """
    异步级联清理视频跨存储资源，推进删除状态机。
    幂等：重复调用不产生额外副作用。
    仅由软删除接口触发（通过 VideoResourceService），禁止在请求线程内直接调用。
    """
    service, db = _create_video_resource_service()
    try:
        video = service.get_video_resource_for_system(video_id=video_id)
        if video is None:
            # 已物理删除或从未存在，视为成功
            logger.info("async_cascade_delete_video: video_id=%s already gone", video_id)
            return {"video_id": video_id, "status": "NOT_FOUND"}

        # 推进状态：PENDING_DELETE -> DELETING
        service.mark_deletion_in_progress(video_id=video_id)

        # 1. OSS 清理
        storage_client = get_object_storage_client()
        if video.oss_key:
            storage_client.delete_object(video.oss_key)
        keyframes_oss_prefix = getattr(video, "keyframes_oss_prefix", None)
        if keyframes_oss_prefix:
            storage_client.delete_prefix(keyframes_oss_prefix)

        # 2. 向量库清理（占位实现；步骤 7 后接入）
        if video.transcript_vector_ids:
            logger.info(
                "async_cascade_delete_video: vector cleanup placeholder for video_id=%s, vector_ids=%s",
                video_id,
                video.transcript_vector_ids,
            )
            # TODO: vector_store.delete_vectors(video.transcript_vector_ids)

        # 3. 数据库物理删除
        service.purge_video(video_id=video_id)

        logger.info("async_cascade_delete_video: video_id=%s purged", video_id)
        return {"video_id": video_id, "deletion_status": "PURGED"}

    except Exception as exc:
        logger.exception("async_cascade_delete_video failed for video_id=%s", video_id)
        try:
            fail_service, fail_db = _create_video_resource_service()
            fail_service.mark_deletion_failed(video_id=video_id)
            fail_db.close()
        except Exception:
            pass
        raise self.retry(exc=exc)
    finally:
        db.close()
