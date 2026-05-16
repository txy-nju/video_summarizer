"""
关键帧抽取任务：异步提取视频关键帧，写入 keyframes（含 oss_key）与 frame_extraction_status。
状态流转：UPLOADED -> EXTRACTING -> COMPLETED / FAILED
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.db.session import SessionLocal
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _create_video_resource_service():
    db = SessionLocal()
    from backend.repositories.video_resource_repository import VideoResourceRepository
    from backend.services.video_resource_service import VideoResourceService

    service = VideoResourceService(repository=VideoResourceRepository(db_session=db))
    return service, db


def _build_oss_key(owner_id: str, video_id: str, frame_filename: str) -> str:
    """按计划约定构造关键帧 OSS 对象键：frames/{owner_id}/{video_id}/{filename}"""
    return f"frames/{owner_id}/{video_id}/{frame_filename}"


def _sanitize_frames_for_db(
    raw_frames: list[dict],
    owner_id: str,
    video_id: str,
) -> list[dict]:
    """
    将 extractor 原始帧列表转换为数据库存储格式：
    - 保留 time, scene_change_score, scene_change_level
    - 将 frame_file 或 image 替换为 oss_key（不持久化 base64）
    """
    result = []
    for i, frame in enumerate(raw_frames):
        frame_file = frame.get("frame_file")
        if frame_file:
            filename = Path(frame_file).name
        else:
            # 按时间戳生成一致的文件名（兼容未启用 ENABLE_KEYFRAME_FILE_REFERENCE 的模式）
            time_str = frame.get("time", "00:00:00").replace(":", "")
            filename = f"frame_{time_str}_{i:04d}.jpg"

        db_frame = {
            "time": frame.get("time", ""),
            "scene_change_score": frame.get("scene_change_score", 0.0),
            "scene_change_level": frame.get("scene_change_level", "none"),
            "oss_key": _build_oss_key(owner_id, video_id, filename),
        }
        result.append(db_frame)
    return result


@celery_app.task(
    bind=True,
    name="backend.tasks.extract_keyframes_tasks.async_extract_keyframes",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def async_extract_keyframes(self, video_id: str) -> dict:
    """
    提取指定视频的关键帧元数据并持久化至数据库。
    base64 图片数据不入库；oss_key 按计划命名规则生成。
    仅由 async_process_video（通过 celery.group）触发，禁止直接调用。
    """
    service, db = _create_video_resource_service()
    try:
        service.mark_frame_extraction_in_progress(video_id=video_id)

        video = service.get_video_resource_for_system(video_id=video_id)
        if video is None:
            logger.error("async_extract_keyframes: video_id=%s not found", video_id)
            return {"video_id": video_id, "status": "NOT_FOUND"}

        video_path = Path(video.oss_key) if video.oss_key else None
        if video_path is None or not video_path.exists():
            raise FileNotFoundError(
                f"Video file not accessible for video_id={video_id}, oss_key={video.oss_key!r}. "
                "Ensure the file is available locally or configure OSS access."
            )

        from config.settings import DEFAULT_FRAME_INTERVAL
        from core.extraction.infrastructure.extractor import MediaExtractor

        extractor = MediaExtractor()
        raw_frames = extractor.extract_frames(video_path, interval=DEFAULT_FRAME_INTERVAL)

        keyframes_for_db = _sanitize_frames_for_db(raw_frames, video.owner_id, video_id)
        oss_prefix = f"frames/{video.owner_id}/{video_id}/"

        service.mark_frame_extraction_completed(
            video_id=video_id,
            keyframes=keyframes_for_db,
            keyframes_oss_prefix=oss_prefix,
        )
        logger.info(
            "async_extract_keyframes completed: video_id=%s, keyframes_count=%d",
            video_id,
            len(keyframes_for_db),
        )
        return {
            "video_id": video_id,
            "status": "COMPLETED",
            "keyframes_count": len(keyframes_for_db),
        }

    except Exception as exc:
        logger.exception("async_extract_keyframes failed for video_id=%s", video_id)
        try:
            fail_service, fail_db = _create_video_resource_service()
            fail_service.mark_frame_extraction_failed(video_id=video_id)
            fail_db.close()
        except Exception:
            pass
        raise self.retry(exc=exc)
    finally:
        db.close()
