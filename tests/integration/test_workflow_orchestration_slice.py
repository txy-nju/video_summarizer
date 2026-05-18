from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from backend.services.workflow_orchestration_service import WorkflowOrchestrationService


@dataclass
class _TaskRecord:
    task_id: str
    owner_id: str
    video_id: str
    workflow_state: str
    title: str | None = None
    draft_summary: str | None = None


class _TaskRepo:
    def __init__(self, record: _TaskRecord):
        self.record = record

    def get_by_owner_and_id(self, owner_id: str, task_id: str):
        if owner_id == self.record.owner_id and task_id == self.record.task_id:
            return self.record
        return None

    def update_state_by_owner_and_id(self, *, owner_id: str, task_id: str, workflow_state: str):
        if not self.get_by_owner_and_id(owner_id, task_id):
            return None
        self.record = replace(self.record, workflow_state=workflow_state)
        return self.record

    def update_by_owner_and_id(self, *, owner_id: str, task_id: str, draft_summary=None, title=None, workflow_state=None):
        if not self.get_by_owner_and_id(owner_id, task_id):
            return None
        if draft_summary is not None:
            self.record = replace(self.record, draft_summary=draft_summary)
        if title is not None:
            self.record = replace(self.record, title=title)
        if workflow_state is not None:
            self.record = replace(self.record, workflow_state=workflow_state)
        return self.record


class _VideoRepo:
    pass


class _ProgressPublisher:
    def __init__(self):
        self.events = []

    def publish_status_update(self, **kwargs):
        self.events.append(("status_update", kwargs))

    def publish_progress(self, **kwargs):
        self.events.append(("progress", kwargs))

    def publish_completed(self, **kwargs):
        self.events.append(("completed", kwargs))

    def publish_error(self, **kwargs):
        self.events.append(("error", kwargs))


class _TaskStatusService:
    pass


class _NotificationService:
    def __init__(self):
        self.calls = []

    def notify_workflow_approval_required(self, **kwargs):
        self.calls.append(("approval_required", kwargs))
        return {"success_count": 0}

    def notify_workflow_completed(self, **kwargs):
        self.calls.append(("completed", kwargs))
        return {"success_count": 0}

    def notify_workflow_failed(self, **kwargs):
        self.calls.append(("failed", kwargs))
        return {"success_count": 0}


def test_workflow_orchestration_slice_analysis_finalize_and_time_travel(monkeypatch):
    task_repo = _TaskRepo(
        _TaskRecord(
            task_id="task-001",
            owner_id="user-1",
            video_id="video-1",
            workflow_state="DRAFT_GENERATING",
            title="测试任务",
        )
    )
    progress = _ProgressPublisher()
    notification = _NotificationService()

    monkeypatch.setattr(
        "backend.services.workflow_orchestration_service.analyze_video",
        lambda **kwargs: {
            "thread_id": "task-001",
            "aggregated_chunk_insights": "analysis-content",
            "editable_aggregated_chunk_insights": "analysis-content",
            "chunk_count": 2,
        },
    )
    monkeypatch.setattr(
        "backend.services.workflow_orchestration_service.finalize_summary",
        lambda **kwargs: "final-summary",
    )
    monkeypatch.setattr(
        "backend.services.workflow_orchestration_service.answer_question_at_timestamp",
        lambda **kwargs: "time-travel-answer",
    )

    service = WorkflowOrchestrationService(
        task_repository=task_repo,
        video_repository=_VideoRepo(),
        progress_publisher=progress,
        task_status_service=_TaskStatusService(),
        notification_service=notification,
    )

    phase1 = asyncio.run(
        service.start_analysis_workflow_async(
            owner_id="user-1",
            task_id="task-001",
            transcript="hello",
            keyframes=[],
            user_initial_preference="pref",
            trace_id="r1",
        )
    )
    assert phase1["workflow_state"] == "WAITING_USER_APPROVAL"
    assert task_repo.record.workflow_state == "WAITING_USER_APPROVAL"

    phase2 = asyncio.run(
        service.start_finalization_workflow_async(
            owner_id="user-1",
            task_id="task-001",
            edited_aggregated_chunk_insights="edited",
            human_guidance="guide",
            trace_id="r1",
        )
    )
    assert phase2 == "final-summary"
    assert task_repo.record.workflow_state == "COMPLETED"

    qa = asyncio.run(
        service.start_time_travel_qa_async(
            owner_id="user-1",
            task_id="task-001",
            timestamp="00:10:00",
            question="what",
            window_seconds=20,
            trace_id="r1",
        )
    )
    assert qa == "time-travel-answer"

    assert any(event[0] == "completed" for event in progress.events)
    assert any(
        call[0] == "approval_required"
        and call[1].get("user_id") == "user-1"
        and call[1].get("task_id") == "task-001"
        and call[1].get("chunk_count") == 2
        for call in notification.calls
    )
    assert any(call[0] == "completed" for call in notification.calls)
