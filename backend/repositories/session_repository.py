from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.models.database import KnowledgeBase, VideoSummaryTask


@dataclass(frozen=True, slots=True)
class VideoTaskSessionRecord:
    task_id: str
    kbid: str
    workflow_state: str
    thread_id: str


class SessionRepository:
    """Read-only repository for workflow session restore scope lookup."""

    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def get_video_task_session(
        self,
        *,
        owner_id: str,
        kbid: str,
        task_id: str,
    ) -> VideoTaskSessionRecord | None:
        """Scope lookup must filter by owner_id + kbid first, then task_id."""
        row = (
            self._session.query(VideoSummaryTask)
            .join(KnowledgeBase, VideoSummaryTask.kbid == KnowledgeBase.kbid)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                VideoSummaryTask.kbid == kbid,
                VideoSummaryTask.task_id == task_id,
            )
            .one_or_none()
        )
        if row is None:
            return None

        workflow_state = str(row.workflow_state.value if hasattr(row.workflow_state, "value") else row.workflow_state)
        return VideoTaskSessionRecord(
            task_id=str(row.task_id),
            kbid=str(row.kbid),
            workflow_state=workflow_state,
            thread_id=str(row.task_id),
        )
