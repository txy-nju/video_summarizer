from __future__ import annotations

from dataclasses import asdict

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.kb_repository import KnowledgeBaseRepository
from backend.repositories.video_resource_repository import VideoResourceRepository
from backend.repositories.video_summary_task_repository import VideoSummaryTaskRecord, VideoSummaryTaskRepository
from backend.schemas.video_summary_task import (
    VideoSummaryTaskCreateRequest,
    VideoSummaryTaskUpdateRequest,
    VideoSummaryTaskView,
)


_WORKFLOW_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT_GENERATING": {"WAITING_USER_APPROVAL", "FAILED"},
    "WAITING_USER_APPROVAL": {"FINAL_GENERATING", "FAILED"},
    "FINAL_GENERATING": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
}


class VideoSummaryTaskService:
    def __init__(
        self,
        repository: VideoSummaryTaskRepository,
        kb_repository: KnowledgeBaseRepository,
        video_repository: VideoResourceRepository,
    ) -> None:
        self._repository = repository
        self._kb_repository = kb_repository
        self._video_repository = video_repository

    def create_video_summary_task(self, *, owner_id: str, payload: VideoSummaryTaskCreateRequest) -> VideoSummaryTaskView | None:
        kb = self._kb_repository.get_by_owner_and_id(owner_id, payload.kbid)
        video = self._video_repository.get_by_owner_and_id(owner_id, payload.video_id)
        if kb is None or video is None:
            return None

        # 验证视频资源已就绪（转录与关键帧抽取均完成）
        if (
            video.extract_completed_at is None
            or video.transcribe_status != "COMPLETED"
            or video.frame_extraction_status != "COMPLETED"
        ):
            raise ValueError("video_not_ready")

        record = self._repository.create(
            owner_id=owner_id,
            kbid=payload.kbid,
            video_id=payload.video_id,
            user_initial_preference=payload.user_initial_preference,
        )
        return self._to_view(record)

    def list_video_summary_tasks(self, *, owner_id: str, page: int, page_size: int) -> tuple[list[VideoSummaryTaskView], dict]:
        records = self._repository.list_by_owner(owner_id)
        normalized_page_size = normalize_page_size(page_size)
        start_index = max(page - 1, 0) * normalized_page_size
        end_index = start_index + normalized_page_size
        page_items = records[start_index:end_index]

        views = [self._to_view(record) for record in page_items]
        pagination = build_pagination(
            page=page,
            page_size=normalized_page_size,
            total=len(records),
            next_cursor=None,
        )
        return views, pagination

    def get_video_summary_task(self, *, owner_id: str, task_id: str) -> VideoSummaryTaskView | None:
        record = self._repository.get_by_owner_and_id(owner_id, task_id)
        if record is None:
            return None
        return self._to_view(record)

    def update_video_summary_task(
        self,
        *,
        owner_id: str,
        task_id: str,
        payload: VideoSummaryTaskUpdateRequest,
    ) -> VideoSummaryTaskView | None:
        record = self._repository.update_by_owner_and_id(
            owner_id=owner_id,
            task_id=task_id,
            draft_summary=payload.draft_summary,
            user_guidance=payload.user_guidance,
            title=payload.title,
        )
        if record is None:
            return None
        return self._to_view(record)

    def delete_video_summary_task(self, *, owner_id: str, task_id: str) -> bool:
        return self._repository.delete_by_owner_and_id(owner_id, task_id)

    def transition_workflow_state(
        self,
        *,
        owner_id: str,
        task_id: str,
        next_state: str,
        draft_summary: str | None = None,
        user_guidance: str | None = None,
        title: str | None = None,
        final_summary: str | None = None,
    ) -> VideoSummaryTaskView | None:
        """System-only transition hook for workflow lifecycle orchestration."""
        record = self._repository.get_by_owner_and_id(owner_id, task_id)
        if record is None:
            return None

        current_state = record.workflow_state
        if current_state == next_state:
            return self._to_view(record)

        allowed = _WORKFLOW_TRANSITIONS.get(current_state, set())
        if next_state not in allowed:
            raise ValueError(f"invalid_workflow_transition:{current_state}->{next_state}")

        if any(value is not None for value in (draft_summary, user_guidance, title, final_summary)):
            updated = self._repository.update_by_owner_and_id(
                owner_id=owner_id,
                task_id=task_id,
                draft_summary=draft_summary,
                user_guidance=user_guidance,
                title=title,
                final_summary=final_summary,
            )
            if updated is None:
                return None

        transitioned = self._repository.update_state_by_owner_and_id(
            owner_id=owner_id,
            task_id=task_id,
            workflow_state=next_state,
        )
        if transitioned is None:
            return None
        return self._to_view(transitioned)

    def mark_analysis_completed(
        self,
        *,
        owner_id: str,
        task_id: str,
        aggregated_chunk_insights: str,
        title: str | None = None,
    ) -> VideoSummaryTaskView | None:
        return self.transition_workflow_state(
            owner_id=owner_id,
            task_id=task_id,
            next_state="WAITING_USER_APPROVAL",
            draft_summary=aggregated_chunk_insights,
            title=title,
        )

    def mark_finalization_started(
        self,
        *,
        owner_id: str,
        task_id: str,
        user_guidance: str | None = None,
    ) -> VideoSummaryTaskView | None:
        return self.transition_workflow_state(
            owner_id=owner_id,
            task_id=task_id,
            next_state="FINAL_GENERATING",
            user_guidance=user_guidance,
        )

    def mark_finalization_completed(
        self,
        *,
        owner_id: str,
        task_id: str,
        final_summary: str,
    ) -> VideoSummaryTaskView | None:
        return self.transition_workflow_state(
            owner_id=owner_id,
            task_id=task_id,
            next_state="COMPLETED",
            final_summary=final_summary,
        )

    def mark_workflow_failed(self, *, owner_id: str, task_id: str) -> VideoSummaryTaskView | None:
        record = self._repository.get_by_owner_and_id(owner_id, task_id)
        if record is None:
            return None
        if record.workflow_state in ("COMPLETED", "FAILED"):
            raise ValueError(f"invalid_workflow_transition:{record.workflow_state}->FAILED")
        return self.transition_workflow_state(
            owner_id=owner_id,
            task_id=task_id,
            next_state="FAILED",
        )

    def _to_view(self, record: VideoSummaryTaskRecord) -> VideoSummaryTaskView:
        payload = asdict(record)
        payload.pop("owner_id", None)
        return VideoSummaryTaskView.model_validate(payload)
