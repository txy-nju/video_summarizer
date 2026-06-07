from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.models.database import KnowledgeBase, VideoResource, VideoSummaryTask


@dataclass(frozen=True, slots=True)
class VideoSummaryTaskRecord:
    task_id: str
    owner_id: str
    kbid: str
    video_id: str
    workflow_state: str
    user_initial_preference: str | None
    draft_summary: str | None
    user_guidance: str | None
    final_summary: str | None
    title: str | None
    summary_vector_ids: list[str] | None
    created_at: datetime
    updated_at: datetime


class VideoSummaryTaskRepository:
    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def create(
        self,
        *,
        owner_id: str,
        kbid: str,
        video_id: str,
        user_initial_preference: str | None,
    ) -> VideoSummaryTaskRecord:
        entity = VideoSummaryTask(
            kbid=kbid,
            video_id=video_id,
            user_initial_preference=user_initial_preference
        )
        self._session.add(entity)
        # Atomic: task row INSERT + ref_count +1 in the SAME transaction
        self._atomic_incr_ref_count(self._session, video_id)
        self._session.commit()
        self._session.refresh(entity)
        return self._to_record(entity, owner_id=owner_id)

    def clone_to_kb(self, *, source_task_id: str, target_kbid: str, owner_id: str) -> VideoSummaryTaskRecord:
        """Clone a Task to another KB with a new task_id.

        All analysis fields (draft/final summary, title, workflow state)
        are copied verbatim.  Only task_id (new UUID) and kbid (target KB)
        differ from the source.  summary_vector_ids is intentionally reset
        to None — the source Task's vector IDs belong to the source KB's
        collection and are invalid in the target KB.

        The caller is responsible for:
        - Validating that the source task and target KB both belong to owner_id
        - Inserting kb_video_relations (if not already present)
        - Dispatching async vector indexing for the target KB

        ref_count is atomically incremented inside this method (same transaction
        as the clone INSERT), so callers MUST NOT increment it again.
        """
        source = (
            self._session.query(VideoSummaryTask)
            .filter(VideoSummaryTask.task_id == source_task_id)
            .one()
        )
        clone = VideoSummaryTask(
            kbid=target_kbid,
            video_id=source.video_id,
            workflow_state=source.workflow_state,
            user_initial_preference=source.user_initial_preference,
            draft_summary=source.draft_summary,
            user_guidance=source.user_guidance,
            final_summary=source.final_summary,
            title=source.title,
            summary_vector_ids=None,
        )
        self._session.add(clone)
        # Atomic: clone INSERT + ref_count +1 in the SAME transaction
        self._atomic_incr_ref_count(self._session, source.video_id)
        self._session.commit()
        self._session.refresh(clone)
        return self._to_record(clone, owner_id=owner_id)

    def list_by_owner(self, owner_id: str) -> list[VideoSummaryTaskRecord]:
        rows = self._session.query(VideoSummaryTask).join(
            KnowledgeBase,
            VideoSummaryTask.kbid == KnowledgeBase.kbid,
        ).join(
            VideoResource,
            VideoSummaryTask.video_id == VideoResource.video_id,
        ).filter(
            KnowledgeBase.owner_id == owner_id,
            VideoResource.owner_id == owner_id,
        ).order_by(VideoSummaryTask.created_at.desc()).all()
        return [self._to_record(row, owner_id=owner_id) for row in rows]

    def list_by_video_id(self, owner_id: str, video_id: str) -> list[VideoSummaryTaskRecord]:
        """List all tasks that reference a specific video (owner-scoped)."""
        rows = (
            self._session.query(VideoSummaryTask)
            .join(KnowledgeBase, VideoSummaryTask.kbid == KnowledgeBase.kbid)
            .join(VideoResource, VideoSummaryTask.video_id == VideoResource.video_id)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                VideoResource.owner_id == owner_id,
                VideoSummaryTask.video_id == video_id,
            )
            .order_by(VideoSummaryTask.created_at.desc())
            .all()
        )
        return [self._to_record(row, owner_id=owner_id) for row in rows]

    def delete_by_video_id(self, owner_id: str, video_id: str) -> int:
        """Cascade-delete all tasks (and their QA records) referencing a video.

        Called by VideoResourceService during video deletion to ensure no
        dangling task references block physical_delete.  Does NOT go through
        VideoSummaryTaskService, so no GC is dispatched — the video deletion
        flow handles all cleanup via async_cascade_delete_video.

        Also resets task_ref_count to 0 in the same transaction so that
        the invariant ref_count == number of live tasks is preserved even
        if the subsequent physical delete fails.

        Returns:
            Number of tasks deleted.
        """
        from backend.models.database import VideoQARecord

        rows = (
            self._session.query(VideoSummaryTask)
            .join(KnowledgeBase, VideoSummaryTask.kbid == KnowledgeBase.kbid)
            .join(VideoResource, VideoSummaryTask.video_id == VideoResource.video_id)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                VideoResource.owner_id == owner_id,
                VideoSummaryTask.video_id == video_id,
            )
            .all()
        )

        deleted_count = len(rows)
        for row in rows:
            self._session.query(VideoQARecord).filter(
                VideoQARecord.task_id == row.task_id
            ).delete(synchronize_session=False)
            self._session.delete(row)

        # Reset ref_count to 0 atomically with the cascade delete.
        # The video is being permanently removed; if physical delete
        # retries, a ref_count of 0 correctly signals "no tasks exist".
        if deleted_count > 0:
            self._session.query(VideoResource).filter(
                VideoResource.video_id == video_id,
            ).update(
                {VideoResource.task_ref_count: 0},
                synchronize_session=False,
            )

        self._session.commit()
        return deleted_count

    def find_by_kb_and_video(self, owner_id: str, kbid: str, video_id: str) -> VideoSummaryTaskRecord | None:
        """Check whether a KB already has a Task for a given video.

        Core duplicate-detection query.  Uses the standard owner-scoped join
        so a user can never see (or collide with) another user's tasks.
        """
        row = (
            self._owned_task_query(owner_id)
            .filter(VideoSummaryTask.kbid == kbid, VideoSummaryTask.video_id == video_id)
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_record(row, owner_id=owner_id)

    def get_by_owner_and_id(self, owner_id: str, task_id: str) -> VideoSummaryTaskRecord | None:
        row = self._owned_task_query(owner_id).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if row is None:
            return None
        return self._to_record(row, owner_id=owner_id)

    # ── 系统级只读查询（后台任务，无 owner_id 上下文）─────────────────

    def get_by_id_system(self, task_id: str) -> VideoSummaryTaskRecord | None:
        """系统级查询：不校验 owner，仅供后台任务使用。

        VideoSummaryTask 自身不存储 owner_id，因此 JOIN KnowledgeBase
        以获取 owner_id 用于构造 Record。

        仅限 Celery 任务、RAG 检索等无用户请求上下文的场景调用。
        有请求上下文的路径必须使用 get_by_owner_and_id() 进行 owner 校验。
        """
        row = (
            self._session.query(VideoSummaryTask, KnowledgeBase.owner_id)
            .join(KnowledgeBase, VideoSummaryTask.kbid == KnowledgeBase.kbid)
            .filter(VideoSummaryTask.task_id == task_id)
            .one_or_none()
        )
        if row is None:
            return None
        entity, kb_owner_id = row
        return self._to_record(entity, owner_id=str(kb_owner_id) if kb_owner_id else "")

    def update_by_owner_and_id(
        self,
        *,
        owner_id: str,
        task_id: str,
        draft_summary: str | None = None,
        user_guidance: str | None = None,
        title: str | None = None,
        final_summary: str | None = None,
        workflow_state: str | None = None,
    ) -> VideoSummaryTaskRecord | None:
        row = self._owned_task_query(owner_id).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if row is None:
            return None

        if draft_summary is not None:
            row.draft_summary = draft_summary
        if user_guidance is not None:
            row.user_guidance = user_guidance
        if title is not None:
            row.title = title
        if final_summary is not None:
            row.final_summary = final_summary
        if workflow_state is not None:
            row.workflow_state = workflow_state

        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row, owner_id=owner_id)

    def delete_by_owner_and_id(self, owner_id: str, task_id: str) -> bool:
        row = self._owned_task_query(owner_id).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if row is None:
            return False

        video_id = str(row.video_id)  # Capture BEFORE row is deleted

        from backend.models.database import VideoQARecord
        self._session.query(VideoQARecord).filter(VideoQARecord.task_id == task_id).delete(synchronize_session=False)

        self._session.delete(row)
        # Atomic: task DELETE + ref_count -1 in the SAME transaction
        self._atomic_decr_ref_count(self._session, video_id)
        self._session.commit()
        return True

    def update_state_by_owner_and_id(
        self,
        *,
        owner_id: str,
        task_id: str,
        workflow_state: str,
    ) -> VideoSummaryTaskRecord | None:
        """Update workflow_state for a task (used by workflow orchestration).

        Args:
            owner_id: User ID for authorization
            task_id: Task ID to update
            workflow_state: New workflow state (DRAFT_GENERATING, WAITING_USER_APPROVAL, FINAL_GENERATING, COMPLETED, FAILED)

        Returns:
            Updated record or None if task not found
        """
        row = self._owned_task_query(owner_id).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if row is None:
            return None

        row.workflow_state = workflow_state
        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row, owner_id=owner_id)

    def _owned_task_query(self, owner_id: str):
        return (
            self._session.query(VideoSummaryTask)
            .join(KnowledgeBase, VideoSummaryTask.kbid == KnowledgeBase.kbid)
            .join(VideoResource, VideoSummaryTask.video_id == VideoResource.video_id)
            .filter(KnowledgeBase.owner_id == owner_id, VideoResource.owner_id == owner_id)
        )

    # ── 原子 ref_count 操作（SQL 级别，避免 lost update）───────────────

    @staticmethod
    def _atomic_incr_ref_count(session: Session, video_id: str) -> int:
        """SQL-level atomic increment. Returns new ref_count or 0 if video missing.

        Uses UPDATE ... SET col = col + 1 so two concurrent sessions always
        produce +2, not a lost-update +1.
        """
        result = (
            session.query(VideoResource)
            .filter(VideoResource.video_id == video_id)
            .update(
                {VideoResource.task_ref_count: VideoResource.task_ref_count + 1},
                synchronize_session=False,
            )
        )
        if result == 0:
            return 0
        row = (
            session.query(VideoResource.task_ref_count)
            .filter(VideoResource.video_id == video_id)
            .one()
        )
        return row[0] or 0

    @staticmethod
    def _atomic_decr_ref_count(session: Session, video_id: str) -> int:
        """SQL-level atomic decrement (floor 0). Returns new ref_count or 0.

        Uses UPDATE ... SET col = col - 1 WHERE col > 0 so the value never
        drops below zero, even under concurrent sessions.
        """
        result = (
            session.query(VideoResource)
            .filter(VideoResource.video_id == video_id)
            .filter(VideoResource.task_ref_count > 0)
            .update(
                {VideoResource.task_ref_count: VideoResource.task_ref_count - 1},
                synchronize_session=False,
            )
        )
        if result == 0:
            return 0
        row = (
            session.query(VideoResource.task_ref_count)
            .filter(VideoResource.video_id == video_id)
            .one()
        )
        return row[0] or 0

    @staticmethod
    def _to_record(
        entity: VideoSummaryTask,
        *,
        owner_id: str,
    ) -> VideoSummaryTaskRecord:
        created = getattr(entity, "created_at", None) or datetime.now(UTC)
        updated = getattr(entity, "updated_at", None) or created
        return VideoSummaryTaskRecord(
            task_id=str(entity.task_id),
            owner_id=owner_id,
            kbid=str(entity.kbid),
            video_id=str(entity.video_id),
            workflow_state=str(entity.workflow_state.value if hasattr(entity.workflow_state, "value") else entity.workflow_state),
            user_initial_preference=entity.user_initial_preference,
            draft_summary=entity.draft_summary,
            user_guidance=entity.user_guidance,
            final_summary=entity.final_summary,
            title=entity.title,
            summary_vector_ids=entity.summary_vector_ids,
            created_at=created,
            updated_at=updated,
        )
