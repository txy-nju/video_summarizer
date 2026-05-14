from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock

try:
    from uuid import uuid7
except ImportError:  # pragma: no cover
    from uuid import uuid4 as uuid7


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
    def __init__(self) -> None:
        self._records_by_owner: dict[str, dict[str, VideoSummaryTaskRecord]] = {}
        self._lock = Lock()

    def create(
        self,
        *,
        owner_id: str,
        kbid: str,
        video_id: str,
        user_initial_preference: str | None,
    ) -> VideoSummaryTaskRecord:
        now = datetime.now(UTC)
        record = VideoSummaryTaskRecord(
            task_id=str(uuid7()),
            owner_id=owner_id,
            kbid=kbid,
            video_id=video_id,
            workflow_state="DRAFT_GENERATING",
            user_initial_preference=user_initial_preference,
            draft_summary=None,
            user_guidance=None,
            final_summary=None,
            title=None,
            summary_vector_ids=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            owner_bucket = self._records_by_owner.setdefault(owner_id, {})
            owner_bucket[record.task_id] = record
        return record

    def list_by_owner(self, owner_id: str) -> list[VideoSummaryTaskRecord]:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            return sorted(owner_bucket.values(), key=lambda item: item.created_at, reverse=True)

    def get_by_owner_and_id(self, owner_id: str, task_id: str) -> VideoSummaryTaskRecord | None:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            return owner_bucket.get(task_id)

    def update_by_owner_and_id(
        self,
        *,
        owner_id: str,
        task_id: str,
        draft_summary: str | None,
        user_guidance: str | None,
        title: str | None,
    ) -> VideoSummaryTaskRecord | None:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            current = owner_bucket.get(task_id)
            if current is None:
                return None
            updated = replace(
                current,
                draft_summary=current.draft_summary if draft_summary is None else draft_summary,
                user_guidance=current.user_guidance if user_guidance is None else user_guidance,
                title=current.title if title is None else title,
                updated_at=datetime.now(UTC),
            )
            owner_bucket[task_id] = updated
            return updated

    def delete_by_owner_and_id(self, owner_id: str, task_id: str) -> bool:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            removed = owner_bucket.pop(task_id, None)
            return removed is not None
