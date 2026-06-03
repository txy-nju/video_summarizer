"""
视频内容加工域调度入口。

async_process_video：内容加工域的顶层 Celery 任务。
- 由 VideoUploadedEvent 监听器触发（禁止上传域直接调用）。
- 通过 celery.group 并行执行转录和关键帧抽取两条独立链路。
- 通过 celery.chord 在两条链路全部完成后汇聚，填充 extract_completed_at。
- 后台可选的向量化任务（async_embed_transcript_chunks_background）独立低优先级队列运行。

async_mark_video_resource_ready：chord 回调，由框架自动调用，禁止外部直接触发。
"""

from __future__ import annotations

import logging

from celery import chord, group

from backend.db.session import SessionLocal
from backend.tasks.base_task import BaseTask
from backend.tasks.celery_app import celery_app
from backend.observability.tracing import make_http_trace_headers

logger = logging.getLogger(__name__)


def _mark_video_resource_ready(video_id: str) -> bool:
    db = SessionLocal()
    try:
        from backend.repositories.video_resource_repository import VideoResourceRepository
        from backend.services.video_resource_service import VideoResourceService

        service = VideoResourceService(repository=VideoResourceRepository(db_session=db))
        return service.mark_extract_completed_if_ready(video_id=video_id)
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="backend.tasks.video_summary_tasks.async_mark_video_resource_ready",
    acks_late=True,
    max_retries=3,
    default_retry_delay=30,
    task_soft_time_limit=120,
    task_time_limit=300,
)
def async_mark_video_resource_ready(self, results: list, video_id: str, trace_id: str = "") -> dict:
    """
    Chord 回调：转录与关键帧抽取并行完成后，填充 extract_completed_at。
    当且仅当两个子任务均状态为 COMPLETED 时才更新，否则仅记录日志。

    Celery 5.x 支持 chord callback 的 bind=True + self.retry()：
    重试时框架会传递相同的 results 参数，回调安全性由内部幂等逻辑保证。
    """
    try:
        marked = _mark_video_resource_ready(video_id)
        if not marked:
            logger.warning("async_mark_video_resource_ready: video_id=%s not found", video_id)
            return {"video_id": video_id, "status": "NOT_FOUND"}
        logger.info("async_mark_video_resource_ready: video_id=%s marked ready trace_id=%s", video_id, trace_id)
        return {"video_id": video_id, "status": "READY", "trace_id": trace_id}
    except Exception as exc:
        logger.exception("async_mark_video_resource_ready failed for video_id=%s trace_id=%s", video_id, trace_id)
        if self.is_last_attempt:
            logger.critical(
                "async_mark_video_resource_ready: retries exhausted for video_id=%s trace_id=%s",
                video_id, trace_id,
            )
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())


@celery_app.task(
    bind=True,
    name="backend.tasks.video_summary_tasks.async_process_video",
    acks_late=True,
    max_retries=2,
    default_retry_delay=10,
    task_soft_time_limit=60,
    task_time_limit=120,
)
def async_process_video(self, video_id: str, trace_id: str = "") -> dict:
    """
    内容加工域入口任务：
    1. 以 celery.group 并行执行转录 + 关键帧抽取。
    2. 以 celery.chord 在两条链路完成后汇聚 → async_mark_video_resource_ready。
    3. （可选）后台向量化任务独立发出，不阻塞主流程。

    触发约定：
    - 仅由内容加工域服务层（VideoResourceService 监听 VideoUploadedEvent）调用。
    - 禁止上传域任务直接调用此任务。

    幂等性守卫：若视频已完成加工（transcribe_status + frame_extraction_status 均为
    COMPLETED），则跳过 dispatch，防止重试造成重复执行。
    """
    from backend.tasks.extract_keyframes_tasks import async_extract_keyframes
    from backend.tasks.transcribe_tasks import async_transcribe_video
    from backend.tasks.vector_tasks import async_embed_transcript_chunks_background

    try:
        # 幂等性守卫：若视频已完全处理则跳过
        if _is_video_already_processed(video_id):
            logger.info(
                "async_process_video: video_id=%s already fully processed, skip dispatch trace_id=%s",
                video_id, trace_id,
            )
            return {"video_id": video_id, "status": "ALREADY_PROCESSED", "trace_id": trace_id}

        task_headers = make_http_trace_headers(trace_id) if trace_id else {}
        extraction_group = group(
            async_transcribe_video.s(video_id, trace_id).set(headers=task_headers),
            async_extract_keyframes.s(video_id, trace_id).set(headers=task_headers),
        )
        pipeline = chord(
            extraction_group,
            async_mark_video_resource_ready.s(video_id, trace_id).set(headers=task_headers),
        )
        pipeline.delay()

        # 后台可选向量化：低优先级队列，不影响主流程
        async_embed_transcript_chunks_background.apply_async(
            args=[video_id, trace_id],
            queue="low_priority",
            countdown=5,
            headers=task_headers,
        )

        logger.info("async_process_video dispatched for video_id=%s trace_id=%s", video_id, trace_id)
        return {"video_id": video_id, "status": "DISPATCHED", "trace_id": trace_id}

    except Exception as exc:
        logger.exception("async_process_video failed for video_id=%s trace_id=%s", video_id, trace_id)
        if self.is_last_attempt:
            logger.critical(
                "async_process_video: retries exhausted for video_id=%s trace_id=%s — "
                "video will NOT enter processing pipeline without manual intervention",
                video_id, trace_id,
            )
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())


def _is_video_already_processed(video_id: str) -> bool:
    """检查视频的转录和抽帧是否均已完成，用于 async_process_video 幂等守卫。"""
    from backend.models.database import VideoResource
    from backend.models.enums import FrameExtractionStatus, TranscribeStatus

    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).first()
        if row is None:
            return False
        return (
            row.transcribe_status == TranscribeStatus.COMPLETED
            and row.frame_extraction_status == FrameExtractionStatus.COMPLETED
        )
    except Exception:
        logger.exception("_is_video_already_processed: check failed for video_id=%s", video_id)
        return False
    finally:
        db.close()
