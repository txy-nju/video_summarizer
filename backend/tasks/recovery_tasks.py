"""
自愈恢复任务：Celery Beat 周期扫描失败的视频并重新入队。

async_scan_and_recover_stuck_videos：每 5 分钟执行一次
- 扫描 DB 中 transcribe_status=FAILED 或 frame_extraction_status=FAILED 的视频
- 按熔断策略（最多 3 次恢复、指数退避间隔）重新分派处理任务
- 超过熔断上限的视频标记为 IRRECOVERABLE，需人工介入

调用的恢复动作：
- 转录失败        → re-dispatch async_transcribe_video
- 抽帧失败        → re-dispatch async_extract_keyframes
- 两者都失败      → re-dispatch async_process_video（全链路调度）
- 删除失败        → re-dispatch async_cascade_delete_video
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.db.session import SessionLocal
from backend.tasks.base_task import BaseTask
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# ── 熔断配置 ─────────────────────────────────────────────────────────────────
MAX_RECOVERY_ATTEMPTS = 3
# 恢复间隔：第 1 次 5 分钟，第 2 次 20 分钟，第 3 次 60 分钟
RECOVERY_BACKOFF_SECONDS = [300, 1200, 3600]

# 标记为 IRRECOVERABLE 的状态值
IRRECOVERABLE_STATUS = "IRRECOVERABLE"


@celery_app.task(
    bind=True,
    name="backend.tasks.recovery_tasks.async_scan_and_recover_stuck_videos",
    acks_late=True,
    max_retries=1,
    default_retry_delay=120,
    task_soft_time_limit=120,
    task_time_limit=300,
)
def async_scan_and_recover_stuck_videos(self) -> dict:
    """
    Celery Beat 周期任务：扫描失败视频并按熔断策略恢复。

    扫描条件：
      - (transcribe_status = FAILED OR frame_extraction_status = FAILED
         OR deletion_status = 'DELETE_FAILED')
      - recovery_attempts < 3
      - last_recovery_at 满足退避间隔（或为 NULL，即首次恢复）
      - is_deleted = False

    恢复后更新 recovery_attempts += 1, last_recovery_at = now()。
    达到 3 次上限后标记 IRRECOVERABLE，跳过后续恢复。
    """
    from backend.models.database import VideoResource
    from backend.models.enums import FrameExtractionStatus, TranscribeStatus

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # 扫描需要恢复的视频（含 FAILED 或 DELETE_FAILED 状态）
        candidates = (
            db.query(VideoResource)
            .filter(
                VideoResource.is_deleted == False,  # noqa: E712
                VideoResource.recovery_attempts < MAX_RECOVERY_ATTEMPTS,
            )
            .filter(
                (VideoResource.transcribe_status == TranscribeStatus.FAILED)
                | (VideoResource.frame_extraction_status == FrameExtractionStatus.FAILED)
                | (VideoResource.deletion_status == "DELETE_FAILED")
            )
            .all()
        )

        if not candidates:
            return {"status": "OK", "message": "No stuck videos found", "recovered": 0, "skipped": 0}

        recovered = 0
        skipped = 0
        irrecoverable = 0

        for video in candidates:
            # 检查退避间隔
            if not _should_recover(video.recovery_attempts, video.last_recovery_at, now):
                skipped += 1
                continue

            # 执行恢复
            success = _dispatch_recovery(video)
            if success:
                video.recovery_attempts += 1
                video.last_recovery_at = now
                recovered += 1
            else:
                skipped += 1

        # 标记超过熔断上限的视频为 IRRECOVERABLE
        irrecoverable = _mark_irrecoverable(db, now)

        db.commit()

        logger.info(
            "async_scan_and_recover_stuck_videos: scanned=%d recovered=%d skipped=%d irrecoverable=%d",
            len(candidates), recovered, skipped, irrecoverable,
        )
        return {
            "status": "OK",
            "scanned": len(candidates),
            "recovered": recovered,
            "skipped": skipped,
            "irrecoverable_marked": irrecoverable,
        }

    except Exception as exc:
        logger.exception("async_scan_and_recover_stuck_videos failed")
        db.rollback()
        if self.is_last_attempt:
            logger.critical("async_scan_and_recover_stuck_videos: retries exhausted — recovery scanner is down")
        raise self.retry(exc=exc, countdown=self.compute_retry_countdown())
    finally:
        db.close()


# ── helpers ──────────────────────────────────────────────────────────────────

def _should_recover(
    recovery_attempts: int,
    last_recovery_at: datetime | None,
    now: datetime,
) -> bool:
    """检查是否满足退避间隔，允许本次恢复操作。"""
    if last_recovery_at is None:
        # 首次恢复，允许
        return True

    index = min(recovery_attempts - 1, len(RECOVERY_BACKOFF_SECONDS) - 1)
    if index < 0:
        index = 0
    required_delay = RECOVERY_BACKOFF_SECONDS[index]

    elapsed = (now - last_recovery_at).total_seconds()
    return elapsed >= required_delay


def _dispatch_recovery(video) -> bool:
    """根据失败类型重新分派对应的处理任务。返回 True 表示成功分派。"""
    from backend.models.enums import FrameExtractionStatus, TranscribeStatus

    video_id = str(video.video_id)
    transcribe_failed = video.transcribe_status == TranscribeStatus.FAILED
    keyframe_failed = video.frame_extraction_status == FrameExtractionStatus.FAILED
    deletion_failed = video.deletion_status == "DELETE_FAILED"

    try:
        if deletion_failed:
            from backend.tasks.video_cleanup_tasks import async_cascade_delete_video
            async_cascade_delete_video.delay(video_id=video_id)
            logger.info("Recovery: re-dispatched async_cascade_delete_video for video_id=%s", video_id)
        elif transcribe_failed and keyframe_failed:
            # 两者都失败 → 全链路重新调度
            from backend.tasks.video_summary_tasks import async_process_video
            async_process_video.delay(video_id=video_id)
            logger.info("Recovery: re-dispatched async_process_video for video_id=%s", video_id)
        elif transcribe_failed:
            from backend.tasks.transcribe_tasks import async_transcribe_video
            async_transcribe_video.delay(video_id=video_id)
            logger.info("Recovery: re-dispatched async_transcribe_video for video_id=%s", video_id)
        elif keyframe_failed:
            from backend.tasks.extract_keyframes_tasks import async_extract_keyframes
            async_extract_keyframes.delay(video_id=video_id)
            logger.info("Recovery: re-dispatched async_extract_keyframes for video_id=%s", video_id)
        else:
            logger.warning(
                "Recovery: video_id=%s has no recoverable failure state "
                "(transcribe=%s, keyframe=%s, deletion=%s)",
                video_id, video.transcribe_status, video.frame_extraction_status, video.deletion_status,
            )
            return False
        return True
    except Exception:
        logger.exception("Recovery: failed to dispatch recovery task for video_id=%s", video_id)
        return False


def _mark_irrecoverable(db, now: datetime) -> int:
    """将超过熔断上限的视频标记为 IRRECOVERABLE。

    条件：recovery_attempts >= MAX_RECOVERY_ATTEMPTS 且状态仍为 FAILED/DELETE_FAILED
    （尚未被手动恢复或人工介入处理）。
    """
    from backend.models.database import VideoResource
    from backend.models.enums import FrameExtractionStatus, TranscribeStatus

    count = 0

    # 转录 FAILED 且超过熔断次数
    stuck_transcribe = (
        db.query(VideoResource)
        .filter(
            VideoResource.is_deleted == False,  # noqa: E712
            VideoResource.recovery_attempts >= MAX_RECOVERY_ATTEMPTS,
            VideoResource.transcribe_status == TranscribeStatus.FAILED,
        )
        .all()
    )
    for video in stuck_transcribe:
        video.transcribe_status = TranscribeStatus.IRRECOVERABLE
        video.last_recovery_at = now
        logger.warning(
            "Marked video_id=%s transcribe_status=IRRECOVERABLE (recovery_attempts=%d)",
            video.video_id, video.recovery_attempts,
        )
        count += 1

    # 抽帧 FAILED 且超过熔断次数
    stuck_keyframe = (
        db.query(VideoResource)
        .filter(
            VideoResource.is_deleted == False,  # noqa: E712
            VideoResource.recovery_attempts >= MAX_RECOVERY_ATTEMPTS,
            VideoResource.frame_extraction_status == FrameExtractionStatus.FAILED,
        )
        .all()
    )
    for video in stuck_keyframe:
        video.frame_extraction_status = FrameExtractionStatus.IRRECOVERABLE
        video.last_recovery_at = now
        logger.warning(
            "Marked video_id=%s frame_extraction_status=IRRECOVERABLE (recovery_attempts=%d)",
            video.video_id, video.recovery_attempts,
        )
        count += 1

    return count
