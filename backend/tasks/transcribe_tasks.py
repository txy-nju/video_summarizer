"""
转录任务：异步将视频音频转录为文本，更新 transcribe_status 与 full_transcript。
状态流转：UPLOADED -> TRANSCRIBING -> COMPLETED / FAILED
"""

from __future__ import annotations

import json
import logging
import os

from backend.db.session import SessionLocal
from backend.infrastructure.storage.oss_client import get_object_storage_client
from backend.tasks.base_task import BaseTask
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
    base=BaseTask,
    name="backend.tasks.transcribe_tasks.async_transcribe_video",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    task_soft_time_limit=600,
    task_time_limit=900,
)
def async_transcribe_video(self, video_id: str, trace_id: str = "") -> dict:
    """
    转录指定视频的音轨，结果写入 video_resources.full_transcript。
    仅由 async_process_video（通过 celery.group）触发，禁止直接调用。
    """
    service, db = _create_video_resource_service()
    try:
        service.mark_transcription_in_progress(video_id=video_id)

        video = service.get_video_resource_for_system(video_id=video_id)
        if video is None:
            logger.error("async_transcribe_video: video_id=%s trace_id=%s not found", video_id, trace_id)
            return {"video_id": video_id, "status": "NOT_FOUND", "trace_id": trace_id}

        if not (video.oss_key and video.oss_key.strip()):
            raise FileNotFoundError(
                f"Video object key missing for video_id={video_id}, oss_key={video.oss_key!r}."
            )

        from core.extraction.infrastructure.extractor import MediaExtractor
        from core.extraction.infrastructure.transcriber import AudioTranscriber

        storage_client = get_object_storage_client()

        extractor = MediaExtractor()
        with storage_client.materialize_to_local_path(video.oss_key) as video_path:
            audio_path = extractor.extract_audio(video_path)

        full_text = ""
        segments = None
        duration = None
        if audio_path:
            api_key = os.getenv("OPENAI_API_KEY", "")
            base_url = os.getenv("OPENAI_BASE_URL") or None
            transcriber = AudioTranscriber(api_key=api_key, base_url=base_url)
            raw = transcriber.transcribe(audio_path)
            try:
                parsed = json.loads(raw)
                full_text = parsed.get("text", raw)
                segments = parsed.get("segments") or None
                if "duration" in parsed:
                    try:
                        duration = int(round(float(parsed["duration"])))
                    except Exception:
                        pass
            except (json.JSONDecodeError, AttributeError):
                full_text = raw
                segments = None

        service.mark_transcription_completed(
            video_id=video_id,
            full_transcript=full_text,
            transcript_segments=segments,
            duration=duration,
        )

        from backend.tasks.vector_tasks import async_embed_transcript_chunks_background
        async_embed_transcript_chunks_background.apply_async(
            args=[video_id],
            kwargs={"trace_id": trace_id},
            queue="low_priority",
        )

        # ── 转录完成后，触发该视频所属所有知识库的向量化 ──
        # 解决竞态问题：用户在视频转录完成前将其加入 KB 时，
        # KB 向量化任务会因 transcript 为空而 SKIPPED。
        # 此处作为兜底，在转录完成后重新触发。
        try:
            from backend.db.session import SessionLocal as _SessionLocal
            from backend.repositories.kb_repository import KnowledgeBaseRepository as _KBRepo

            _db = _SessionLocal()
            try:
                _kbids = _KBRepo(db_session=_db).get_linked_kbid_by_video_system(video_id)
            finally:
                _db.close()

            if _kbids:
                from backend.tasks.global_retrieval_tasks import async_add_video_to_vector_collection

                for _kbid in _kbids:
                    async_add_video_to_vector_collection.apply_async(
                        args=[_kbid, video_id],
                        queue="low_priority",
                    )
                logger.info(
                    "async_transcribe_video: dispatched KB vectorization for video_id=%s, kbids=%s",
                    video_id, _kbids,
                )
        except Exception:
            logger.exception(
                "async_transcribe_video: failed to dispatch KB vectorization for video_id=%s",
                video_id,
            )

        logger.info(
            "async_transcribe_video completed: video_id=%s, trace_id=%s, transcript_length=%d",
            video_id,
            trace_id,
            len(full_text),
        )
        return {"video_id": video_id, "status": "COMPLETED", "trace_id": trace_id, "transcript_length": len(full_text)}

    except Exception as exc:
        logger.exception("async_transcribe_video failed for video_id=%s trace_id=%s", video_id, trace_id)
        # 仅在重试耗尽时标记 FAILED；重试期间保留原状态让 WebSocket 展示"重试中"
        if self.is_last_attempt:
            logger.critical(
                "async_transcribe_video: retries exhausted for video_id=%s trace_id=%s",
                video_id, trace_id,
            )
            try:
                fail_service, fail_db = _create_video_resource_service()
                fail_service.mark_transcription_failed(video_id=video_id)
                fail_db.close()
            except Exception:
                pass
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())
    finally:
        db.close()
