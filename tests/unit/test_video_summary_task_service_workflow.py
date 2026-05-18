from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from backend.repositories.video_summary_task_repository import VideoSummaryTaskRecord
from backend.services.video_summary_task_service import VideoSummaryTaskService


class _StubKbRepo:
    def get_by_owner_and_id(self, owner_id: str, kbid: str):
        return None


class _StubVideoRepo:
    def get_by_owner_and_id(self, owner_id: str, video_id: str):
        return None


class _FakeTaskRepo:
    def __init__(self, seed: VideoSummaryTaskRecord) -> None:
        self.record = seed

    def get_by_owner_and_id(self, owner_id: str, task_id: str) -> VideoSummaryTaskRecord | None:
        if owner_id != self.record.owner_id or task_id != self.record.task_id:
            return None
        return self.record

    def update_by_owner_and_id(
        self,
        *,
        owner_id: str,
        task_id: str,
        draft_summary: str | None,
        user_guidance: str | None,
        title: str | None,
    ) -> VideoSummaryTaskRecord | None:
        if owner_id != self.record.owner_id or task_id != self.record.task_id:
            return None
        self.record = replace(
            self.record,
            draft_summary=draft_summary if draft_summary is not None else self.record.draft_summary,
            user_guidance=user_guidance if user_guidance is not None else self.record.user_guidance,
            title=title if title is not None else self.record.title,
            updated_at=datetime.now(UTC),
        )
        return self.record

    def update_state_by_owner_and_id(self, *, owner_id: str, task_id: str, workflow_state: str) -> VideoSummaryTaskRecord | None:
        if owner_id != self.record.owner_id or task_id != self.record.task_id:
            return None
        self.record = replace(self.record, workflow_state=workflow_state, updated_at=datetime.now(UTC))
        return self.record


def _seed_record() -> VideoSummaryTaskRecord:
    now = datetime.now(UTC)
    return VideoSummaryTaskRecord(
        task_id="task-1",
        owner_id="user-1",
        kbid="kb-1",
        video_id="video-1",
        workflow_state="DRAFT_GENERATING",
        user_initial_preference=None,
        draft_summary=None,
        user_guidance=None,
        final_summary=None,
        title=None,
        summary_vector_ids=None,
        created_at=now,
        updated_at=now,
    )


def test_transition_workflow_state_enforces_monotonic_path() -> None:
    repo = _FakeTaskRepo(_seed_record())
    service = VideoSummaryTaskService(repository=repo, kb_repository=_StubKbRepo(), video_repository=_StubVideoRepo())

    service.mark_analysis_completed(
        owner_id="user-1",
        task_id="task-1",
        aggregated_chunk_insights="phase-1 insights",
        title="总结-task-1",
    )
    assert repo.record.workflow_state == "WAITING_USER_APPROVAL"
    assert repo.record.draft_summary == "phase-1 insights"

    service.mark_finalization_started(
        owner_id="user-1",
        task_id="task-1",
        user_guidance="强调结论",
    )
    assert repo.record.workflow_state == "FINAL_GENERATING"

    service.mark_finalization_completed(
        owner_id="user-1",
        task_id="task-1",
        final_summary="final output",
    )
    assert repo.record.workflow_state == "COMPLETED"
    assert repo.record.draft_summary == "final output"


def test_transition_workflow_state_rejects_invalid_jump() -> None:
    repo = _FakeTaskRepo(_seed_record())
    service = VideoSummaryTaskService(repository=repo, kb_repository=_StubKbRepo(), video_repository=_StubVideoRepo())

    with pytest.raises(ValueError, match="invalid_workflow_transition"):
        service.transition_workflow_state(
            owner_id="user-1",
            task_id="task-1",
            next_state="COMPLETED",
        )
