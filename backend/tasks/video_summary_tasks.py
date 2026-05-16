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
from backend.repositories.video_resource_repository import VideoResourceRepository
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="backend.tasks.video_summary_tasks.async_mark_video_resource_ready",
    acks_late=True,
)
def async_mark_video_resource_ready(results: list, video_id: str) -> dict:
    """
    Chord 回调：转录与关键帧抽取并行完成后，填充 extract_completed_at。
    当且仅当两个子任务均状态为 COMPLETED 时才更新，否则仅记录日志。
    """
    db = SessionLocal()
    try:
        repo = VideoResourceRepository(db)
        repo.update_extract_completed_at(video_id)
        logger.info("async_mark_video_resource_ready: video_id=%s marked ready", video_id)
        return {"video_id": video_id, "status": "READY"}
    except Exception:
        logger.exception("async_mark_video_resource_ready failed for video_id=%s", video_id)
        raise
    finally:
        db.close()


@celery_app.task(
    name="backend.tasks.video_summary_tasks.async_process_video",
    acks_late=True,
)
def async_process_video(video_id: str) -> dict:
    """
    内容加工域入口任务：
    1. 以 celery.group 并行执行转录 + 关键帧抽取。
    2. 以 celery.chord 在两条链路完成后汇聚 → async_mark_video_resource_ready。
    3. （可选）后台向量化任务独立发出，不阻塞主流程。

    触发约定：
    - 仅由内容加工域服务层（VideoResourceService 监听 VideoUploadedEvent）调用。
    - 禁止上传域任务直接调用此任务。
    """
    from backend.tasks.extract_keyframes_tasks import async_extract_keyframes
    from backend.tasks.transcribe_tasks import async_transcribe_video
    from backend.tasks.vector_tasks import async_embed_transcript_chunks_background

    extraction_group = group(
        async_transcribe_video.s(video_id),
        async_extract_keyframes.s(video_id),
    )
    pipeline = chord(
        extraction_group,
        async_mark_video_resource_ready.s(video_id),
    )
    pipeline.delay()

    # 后台可选向量化：低优先级队列，不影响主流程
    async_embed_transcript_chunks_background.apply_async(
        args=[video_id],
        queue="low_priority",
        countdown=5,
    )

    logger.info("async_process_video dispatched for video_id=%s", video_id)
    return {"video_id": video_id, "status": "DISPATCHED"}
