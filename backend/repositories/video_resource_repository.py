from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.models.database import VideoResource, kb_video_relation_table
from backend.models.enums import FrameExtractionStatus, TranscribeStatus


@dataclass(frozen=True, slots=True)
class VideoResourceRecord:
    video_id: str
    owner_id: str
    file_name: str
    oss_key: str
    duration: int
    full_transcript: str | None
    transcript_segments: list | None
    transcribe_status: str
    transcript_vector_ids: list[str] | None
    keyframes: list[dict] | None
    frame_extraction_status: str
    keyframes_oss_prefix: str | None
    extract_completed_at: datetime | None
    file_hash: str | None
    task_ref_count: int
    is_deleted: bool
    deleted_at: datetime | None
    deletion_status: str
    created_at: datetime


class VideoResourceRepository:
    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def create(self, *, owner_id: str, file_name: str) -> VideoResourceRecord:
        entity = VideoResource(
            owner_id=owner_id,
            file_name=file_name,
            oss_key="",
            duration=0,
            full_transcript=None,
            transcribe_status=TranscribeStatus.UPLOADED,
            transcript_vector_ids=None,
            keyframes=None,
            frame_extraction_status=FrameExtractionStatus.UPLOADED,
            keyframes_oss_prefix=None,
            extract_completed_at=None,
        )
        # 模型中已定义软删除字段时，显式初始化；兼容未完成迁移场景
        if hasattr(entity, "is_deleted"):
            entity.is_deleted = False
        if hasattr(entity, "deleted_at"):
            entity.deleted_at = None
        if hasattr(entity, "deletion_status"):
            entity.deletion_status = "NONE"

        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return self._to_record(entity)

    def list_by_owner(self, owner_id: str) -> list[VideoResourceRecord]:
        query = self._session.query(VideoResource).filter(VideoResource.owner_id == owner_id)
        if hasattr(VideoResource, "is_deleted"):
            query = query.filter(VideoResource.is_deleted.is_(False))

        rows = query.order_by(VideoResource.created_at.desc()).all()
        return [self._to_record(row) for row in rows]

    def get_by_owner_and_id(self, owner_id: str, video_id: str) -> VideoResourceRecord | None:
        query = self._session.query(VideoResource).filter(
            VideoResource.owner_id == owner_id,
            VideoResource.video_id == video_id,
        )
        if hasattr(VideoResource, "is_deleted"):
            query = query.filter(VideoResource.is_deleted.is_(False))

        row = query.one_or_none()
        if row is None:
            return None
        return self._to_record(row)

    def update_by_owner_and_id(
        self,
        *,
        owner_id: str,
        video_id: str,
        file_name: str | None,
    ) -> VideoResourceRecord | None:
        query = self._session.query(VideoResource).filter(
            VideoResource.owner_id == owner_id,
            VideoResource.video_id == video_id,
        )
        if hasattr(VideoResource, "is_deleted"):
            query = query.filter(VideoResource.is_deleted.is_(False))

        row = query.one_or_none()
        if row is None:
            return None

        if file_name is not None:
            row.file_name = file_name

        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row)

    def delete_by_owner_and_id(self, owner_id: str, video_id: str) -> bool:
        row = self._session.query(VideoResource).filter(
            VideoResource.owner_id == owner_id,
            VideoResource.video_id == video_id,
        ).one_or_none()

        if row is None:
            return False

        # 若模型已具备删除生命周期字段，则执行软删除；否则退化为物理删除保持接口语义。
        if hasattr(row, "is_deleted"):
            if bool(getattr(row, "is_deleted", False)):
                return False
            row.is_deleted = True
            if hasattr(row, "deleted_at"):
                row.deleted_at = datetime.now(UTC)
            if hasattr(row, "deletion_status"):
                row.deletion_status = "PENDING_DELETE"

            # Soft delete contract: strip KB-video relations in the same transaction
            # so retrieval whitelist no longer includes deleted videos.
            self._session.execute(
                delete(kb_video_relation_table).where(kb_video_relation_table.c.video_id == video_id)
            )
        else:
            self._session.delete(row)

        self._session.commit()
        return True

    # -------------------------------------------------------------------------
    # System-only methods (Celery tasks, not request-scoped)
    # These bypass owner check and are for background workers only.
    # -------------------------------------------------------------------------

    def get_linked_kb_ids_for_video(self, video_id: str) -> list[str]:
        """查询视频关联的所有知识库 ID（用于删除前收集，以便异步清理 per-KB 向量）。

        调用方必须在 delete_by_owner_and_id 之前调用，因为软删除会清空关联关系。
        """
        rows = self._session.query(kb_video_relation_table.c.kbid).filter(
            kb_video_relation_table.c.video_id == video_id
        ).all()
        return [row[0] for row in rows]

    def get_by_id_system(self, video_id: str) -> VideoResourceRecord | None:
        """Bypass owner check; for background worker use only."""
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is None:
            return None
        return self._to_record(row)

    def update_transcription_status(
        self,
        video_id: str,
        status: TranscribeStatus,
        *,
        full_transcript: str | None = None,
        transcript_segments: list | None = None,
        duration: int | None = None,
    ) -> None:
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is None:
            return
        row.transcribe_status = status
        if full_transcript is not None:
            row.full_transcript = full_transcript
        if transcript_segments is not None:
            row.transcript_segments = transcript_segments
        if duration is not None:
            row.duration = duration
        self._session.commit()

    def update_frame_extraction(
        self,
        video_id: str,
        status: FrameExtractionStatus,
        *,
        keyframes: list[dict] | None = None,
        keyframes_oss_prefix: str | None = None,
    ) -> None:
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is None:
            return
        row.frame_extraction_status = status
        if keyframes is not None:
            row.keyframes = keyframes
        if keyframes_oss_prefix is not None:
            row.keyframes_oss_prefix = keyframes_oss_prefix
        self._session.commit()

    def update_extract_completed_at(self, video_id: str) -> None:
        """Set extract_completed_at when both transcription and frame extraction are done."""
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is None:
            return
        if (
            row.transcribe_status == TranscribeStatus.COMPLETED
            and row.frame_extraction_status == FrameExtractionStatus.COMPLETED
        ):
            row.extract_completed_at = datetime.now(UTC)
            self._session.commit()

    def update_deletion_status(self, video_id: str, deletion_status: str) -> None:
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is None:
            return
        if hasattr(row, "deletion_status"):
            row.deletion_status = deletion_status
        self._session.commit()

    def physical_delete(self, video_id: str) -> None:
        """Physical delete after all external resources are cleaned up.

        Guard: only proceed if no VideoSummaryTask rows still reference this video.
        """
        from backend.models.database import VideoSummaryTask

        task_count = (
            self._session.query(VideoSummaryTask)
            .filter(VideoSummaryTask.video_id == video_id)
            .count()
        )
        if task_count > 0:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning(
                "physical_delete aborted for video_id=%s: %d tasks still reference it",
                video_id, task_count,
            )
            return

        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is not None:
            self._session.delete(row)
            self._session.commit()

    # -------------------------------------------------------------------------
    # Hash-based dedup methods
    # -------------------------------------------------------------------------

    def get_by_owner_and_hash(self, owner_id: str, file_hash: str) -> VideoResourceRecord | None:
        """Find a non-deleted video by owner + file hash (dedup lookup)."""
        query = self._session.query(VideoResource).filter(
            VideoResource.owner_id == owner_id,
            VideoResource.file_hash == file_hash,
        )
        if hasattr(VideoResource, "is_deleted"):
            query = query.filter(VideoResource.is_deleted.is_(False))

        row = query.one_or_none()
        if row is None:
            return None
        return self._to_record(row)

    def set_file_hash(self, video_id: str, file_hash: str) -> None:
        """Write SHA256 hash on a VideoResource record (system-only)."""
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is not None:
            row.file_hash = file_hash
            self._session.commit()

    # -------------------------------------------------------------------------
    # Reference counting
    # -------------------------------------------------------------------------

    def increment_ref_count(self, video_id: str) -> int:
        """Atomically increment task_ref_count. Returns new value.

        .. deprecated::
            Task-related ref_count management is now handled inside
            ``VideoSummaryTaskRepository`` (same transaction as the task
            row mutation via SQL-level atomic UPDATE).  This method
            remains available for non-task callers but SHOULD NOT be
            called from ``VideoSummaryTaskService``.
        """
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is None:
            return 0
        row.task_ref_count = (row.task_ref_count or 0) + 1
        self._session.commit()
        return row.task_ref_count

    def decrement_ref_count(self, video_id: str) -> int:
        """Atomically decrement task_ref_count (floor 0). Returns new value.

        .. deprecated::
            Task-related ref_count management is now handled inside
            ``VideoSummaryTaskRepository`` (same transaction as the task
            row mutation via SQL-level atomic UPDATE).  This method
            remains available for non-task callers but SHOULD NOT be
            called from ``VideoSummaryTaskService``.
        """
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is None:
            return 0
        if row.task_ref_count is not None and row.task_ref_count > 0:
            row.task_ref_count -= 1
        self._session.commit()
        self._session.refresh(row)
        return row.task_ref_count or 0

    def decrement_ref_count_bulk(self, video_id_counts: dict[str, int]) -> None:
        """Bulk-decrement ref counts (used during KB cascade deletion).

        Args:
            video_id_counts: dict mapping video_id -> number of tasks being deleted.
        """
        for video_id, count in video_id_counts.items():
            self._session.query(VideoResource).filter(
                VideoResource.video_id == video_id,
            ).update(
                {VideoResource.task_ref_count: VideoResource.task_ref_count - count},
                synchronize_session=False,
            )
        self._session.commit()

    def get_ref_count(self, video_id: str) -> int:
        """Read current task_ref_count for a video."""
        row = self._session.query(VideoResource).filter(
            VideoResource.video_id == video_id,
        ).one_or_none()
        if row is None:
            return 0
        return row.task_ref_count or 0

    @staticmethod
    def _to_record(entity: VideoResource) -> VideoResourceRecord:
        created_at = getattr(entity, "created_at", None) or datetime.now(UTC)
        return VideoResourceRecord(
            video_id=str(entity.video_id),
            owner_id=str(entity.owner_id),
            file_name=entity.file_name,
            oss_key=entity.oss_key or "",
            duration=entity.duration or 0,
            full_transcript=entity.full_transcript,
            transcript_segments=getattr(entity, "transcript_segments", None),
            transcribe_status=str(entity.transcribe_status.value if hasattr(entity.transcribe_status, "value") else entity.transcribe_status),
            transcript_vector_ids=entity.transcript_vector_ids,
            keyframes=entity.keyframes,
            frame_extraction_status=str(entity.frame_extraction_status.value if hasattr(entity.frame_extraction_status, "value") else entity.frame_extraction_status),
            keyframes_oss_prefix=entity.keyframes_oss_prefix,
            extract_completed_at=entity.extract_completed_at,
            file_hash=getattr(entity, "file_hash", None),
            task_ref_count=int(getattr(entity, "task_ref_count", 0) or 0),
            is_deleted=bool(getattr(entity, "is_deleted", False)),
            deleted_at=getattr(entity, "deleted_at", None),
            deletion_status=str(getattr(entity, "deletion_status", "NONE")),
            created_at=created_at,
        )
