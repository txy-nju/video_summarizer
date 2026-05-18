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
        self._session.commit()
        self._session.refresh(entity)
        return self._to_record(entity, owner_id=owner_id)

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

    def get_by_owner_and_id(self, owner_id: str, task_id: str) -> VideoSummaryTaskRecord | None:
        row = self._owned_task_query(owner_id).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if row is None:
            return None
        return self._to_record(row, owner_id=owner_id)

    def update_by_owner_and_id(
        self,
        *,
        owner_id: str,
        task_id: str,
        draft_summary: str | None,
        user_guidance: str | None,
        title: str | None,
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

        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row, owner_id=owner_id)

    def delete_by_owner_and_id(self, owner_id: str, task_id: str) -> bool:
        row = self._owned_task_query(owner_id).filter(VideoSummaryTask.task_id == task_id).one_or_none()
        if row is None:
            return False

        self._session.delete(row)
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
