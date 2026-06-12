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

from backend.schemas.video_format import validate_video_magic_bytes
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
    base=BaseTask,
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

    幂等性：若上传已处于终态（done/dedup_reused/rejected），直接返回。
    Celery 重试安全：_get_session_video_id 检查 Redis 会话中已有的 video_id，
    若前次执行已创建 VideoResource，重试时直接复用该 ID，跳过重复创建。
    OSS 上传覆盖写，consumer 侧通过 DB 状态判断跳过重复处理。
    """
    service = _create_upload_service()
    try:
        result = service.finalize_upload(upload_id=upload_id)

        if result.get("status") == "MERGED":
            owner_id = result.get("owner_id", "")
            file_name = result.get("file_name", "")
            merged_path = result.get("merged_path", "")

            # Step 0: Validate merged file is a real video (magic bytes, 第二道防线)
            is_valid, detected_type = validate_video_magic_bytes(merged_path)
            if not is_valid:
                logger.warning(
                    "Format validation failed: upload_id=%s, file_name=%s, detected=%s",
                    upload_id, file_name, detected_type,
                )
                _abort_video_resource(result.get("video_id"))
                _cleanup_upload_session(upload_id, final_state="rejected")
                Path(merged_path).unlink(missing_ok=True)
                return {
                    "upload_id": upload_id,
                    "status": "REJECTED",
                    "reason": f"文件格式校验失败，检测到 {detected_type}",
                    "trace_id": trace_id,
                }

            # Step 0.5: Compute SHA256 hash of merged file for dedup
            file_hash = _compute_sha256(merged_path)

            # Step 0.5: Dedup check — reuse existing video if same hash exists
            existing_video_id = _find_existing_by_hash(owner_id=owner_id, file_hash=file_hash)
            if existing_video_id is not None:
                logger.info(
                    "Hash dedup: reusing existing video_id=%s for hash=%s, skipping file storage",
                    existing_video_id, file_hash[:16],
                )
                _ensure_file_hash(existing_video_id, file_hash)
                # Clean up orphan pre-registered record if it differs from the dedup match
                pre_registered_id = result.get("video_id")
                if pre_registered_id and pre_registered_id != existing_video_id:
                    _abort_video_resource(pre_registered_id)
                _cleanup_upload_session(upload_id, video_id=existing_video_id, final_state="dedup_reused")
                existing_oss_key = _get_existing_oss_key(existing_video_id)
                return {
                    "upload_id": upload_id,
                    "video_id": existing_video_id,
                    "status": "DEDUP_REUSED",
                    "oss_key": existing_oss_key,
                    "trace_id": trace_id,
                }

            # Step 1: 创建 VideoResource 记录
            # 幂等保护：先检查 Redis 会话是否已有 video_id（Celery 重试场景）
            video_id = _get_session_video_id(upload_id) or _create_video_resource(
                owner_id=owner_id,
                file_name=file_name,
            )
            if video_id is None:
                logger.error("async_finalize_upload: failed to create video_resource for upload_id=%s", upload_id)
                return {"upload_id": upload_id, "status": "FAILED", "error": "Failed to create video_resource"}

            # Step 1.5: Write file_hash on the record
            _set_video_resource_file_hash(video_id, file_hash)

            # Step 2: 上传合并文件到本地存储（覆盖写，幂等）
            object_key = _build_video_object_key(
                owner_id=owner_id, video_id=video_id, file_name=file_name, merged_path=merged_path
            )
            from backend.infrastructure.storage.oss_client import get_object_storage_client

            storage_client = get_object_storage_client()
            stored_key = storage_client.upload_file(local_path=Path(merged_path), object_key=object_key)
            _set_video_resource_oss_key(video_id=video_id, oss_key=stored_key)

            # Step 3: 清理分片文件并写入最终 video_id 到会话
            _cleanup_upload_session(upload_id, video_id=video_id, final_state="done")

            # Step 4: 发布 VideoUploadedEvent
            _publish_video_uploaded_event(
                video_id=video_id, owner_id=owner_id, oss_key=stored_key, trace_id=trace_id
            )

            logger.info(
                "async_finalize_upload completed: upload_id=%s, video_id=%s, oss_key=%s, trace_id=%s, hash=%s",
                upload_id, video_id, stored_key, trace_id, file_hash[:16],
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
) -> str | None:
    """Create a new VideoResource record.

    Always creates a fresh record — no pre-registration matching.
    The hash-based dedup check (Step 2) runs BEFORE this function,
    so if we reach here, a new record is always appropriate.
    """
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
        logger.info(
            "Created VideoResource: video_id=%s, file_name=%s, owner_id=%s",
            view.video_id, file_name, owner_id,
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


def _abort_video_resource(video_id: str | None) -> None:
    """Soft-delete a pre-registered VideoResource record when the upload is rejected.

    Only acts when *video_id* is non-empty; does nothing otherwise.
    """
    if not video_id:
        return
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource

    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        if row is not None:
            row.is_deleted = True
            db.commit()
            logger.info("Aborted VideoResource video_id=%s (soft-deleted)", video_id)
    except Exception:
        logger.exception("Failed to abort VideoResource video_id=%s", video_id)
    finally:
        db.close()


# ── Hash Dedup Helpers ──────────────────────────────────────────────────────────


def _compute_sha256(file_path: str) -> str:
    """Compute SHA-256 hex digest of a file (streaming, handles large files)."""
    import hashlib
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _find_existing_by_hash(*, owner_id: str, file_hash: str) -> str | None:
    """Find a non-deleted video with the same hash for the same owner."""
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource

    db = SessionLocal()
    try:
        row = (
            db.query(VideoResource)
            .filter(
                VideoResource.owner_id == owner_id,
                VideoResource.file_hash == file_hash,
                VideoResource.is_deleted.is_(False),
            )
            .first()
        )
        return str(row.video_id) if row else None
    finally:
        db.close()


def _set_video_resource_file_hash(video_id: str, file_hash: str) -> None:
    """Write SHA256 hash on a VideoResource record."""
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource

    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        if row is not None:
            row.file_hash = file_hash
            db.commit()
    finally:
        db.close()


def _ensure_file_hash(video_id: str, file_hash: str) -> None:
    """Write file_hash only if currently NULL (belt-and-suspenders for dedup path)."""
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource

    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        if row is not None and not row.file_hash:
            row.file_hash = file_hash
            db.commit()
    finally:
        db.close()


def _get_existing_oss_key(video_id: str) -> str:
    """Read oss_key from an existing VideoResource record."""
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource

    db = SessionLocal()
    try:
        row = db.query(VideoResource).filter(VideoResource.video_id == video_id).one_or_none()
        return row.oss_key if row else ""
    finally:
        db.close()


def _cleanup_upload_session(
    upload_id: str,
    video_id: str | None = None,
    final_state: str = "done",
) -> None:
    """Clean up Redis upload session chunks and set final video_id + state.

    Args:
        upload_id: The upload session to clean up.
        video_id: Final VideoResource ID to write into the session (dedup-reused or newly created).
        final_state: Terminal state to set (done / dedup_reused / rejected).
    """
    from backend.repositories.upload_repository import UploadRepository
    import redis as redis_lib
    import os

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    repo = UploadRepository(redis_client)
    repo.cleanup_chunks(upload_id)
    repo.finalize_session(upload_id, video_id=video_id, final_state=final_state)


def _get_session_video_id(upload_id: str) -> str | None:
    """Read the video_id already persisted on the Redis upload session.

    Used for Celery-retry idempotency: if a prior execution of
    async_finalize_upload created a VideoResource and wrote its id back
    into the session, a retry can skip record creation and resume from
    the oss_key upload step.
    """
    from backend.repositories.upload_repository import UploadRepository
    import redis as redis_lib
    import os

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/2")
    redis_client = redis_lib.Redis.from_url(redis_url, decode_responses=True)
    state = UploadRepository(redis_client).get_session(upload_id)
    if state is None:
        return None
    return state.video_id or None


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
