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
            workflow_state=payload.workflow_state,
            user_guidance=payload.user_guidance,
            title=payload.title,
        )
        if record is None:
            return None
        return self._to_view(record)

    def delete_video_summary_task(self, *, owner_id: str, task_id: str) -> bool:
        return self._repository.delete_by_owner_and_id(owner_id, task_id)

    def _to_view(self, record: VideoSummaryTaskRecord) -> VideoSummaryTaskView:
        payload = asdict(record)
        payload.pop("owner_id", None)
        return VideoSummaryTaskView.model_validate(payload)
