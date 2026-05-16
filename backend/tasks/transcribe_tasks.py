"""
转录任务：异步将视频音频转录为文本，更新 transcribe_status 与 full_transcript。
状态流转：UPLOADED -> TRANSCRIBING -> COMPLETED / FAILED
"""

from __future__ import annotations

import logging
import os
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


@celery_app.task(
    bind=True,
    name="backend.tasks.transcribe_tasks.async_transcribe_video",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def async_transcribe_video(self, video_id: str) -> dict:
    """
    转录指定视频的音轨，结果写入 video_resources.full_transcript。
    仅由 async_process_video（通过 celery.group）触发，禁止直接调用。
    """
    service, db = _create_video_resource_service()
    try:
        service.mark_transcription_in_progress(video_id=video_id)

        video = service.get_video_resource_for_system(video_id=video_id)
        if video is None:
            logger.error("async_transcribe_video: video_id=%s not found", video_id)
            return {"video_id": video_id, "status": "NOT_FOUND"}

        # 通过 oss_key 定位本地视频文件（step 5.5 后替换为 OSS 下载）
        video_path = Path(video.oss_key) if video.oss_key else None
        if video_path is None or not video_path.exists():
            raise FileNotFoundError(
                f"Video file not accessible for video_id={video_id}, oss_key={video.oss_key!r}. "
                "Ensure the file is available locally or configure OSS access."
            )

        from core.extraction.infrastructure.extractor import MediaExtractor
        from core.extraction.infrastructure.transcriber import AudioTranscriber

        extractor = MediaExtractor()
        audio_path = extractor.extract_audio(video_path)

        transcript = ""
        if audio_path:
            api_key = os.getenv("OPENAI_API_KEY", "")
            base_url = os.getenv("OPENAI_BASE_URL") or None
            transcriber = AudioTranscriber(api_key=api_key, base_url=base_url)
            transcript = transcriber.transcribe(audio_path)

        service.mark_transcription_completed(video_id=video_id, full_transcript=transcript)
        logger.info(
            "async_transcribe_video completed: video_id=%s, transcript_length=%d",
            video_id,
            len(transcript),
        )
        return {"video_id": video_id, "status": "COMPLETED", "transcript_length": len(transcript)}

    except Exception as exc:
        logger.exception("async_transcribe_video failed for video_id=%s", video_id)
        # 写入 FAILED 状态（幂等，忽略二次错误）
        try:
            fail_service, fail_db = _create_video_resource_service()
            fail_service.mark_transcription_failed(video_id=video_id)
            fail_db.close()
        except Exception:
            pass
        raise self.retry(exc=exc)
    finally:
        db.close()
