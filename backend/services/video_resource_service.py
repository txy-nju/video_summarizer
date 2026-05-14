from __future__ import annotations

from dataclasses import asdict

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.video_resource_repository import VideoResourceRecord, VideoResourceRepository
from backend.schemas.video_resource import KeyFrameItem, VideoResourceCreateRequest, VideoResourceUpdateRequest, VideoResourceView


class VideoResourceService:
    def __init__(self, repository: VideoResourceRepository) -> None:
        self._repository = repository

    def create_video_resource(self, *, owner_id: str, payload: VideoResourceCreateRequest) -> VideoResourceView:
        record = self._repository.create(
            owner_id=owner_id,
            file_name=payload.file_name,
            oss_key=payload.oss_key,
            duration=payload.duration,
        )
        return self._to_view(record)

    def list_video_resources(self, *, owner_id: str, page: int, page_size: int) -> tuple[list[VideoResourceView], dict]:
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

    def get_video_resource(self, *, owner_id: str, video_id: str) -> VideoResourceView | None:
        record = self._repository.get_by_owner_and_id(owner_id, video_id)
        if record is None:
            return None
        return self._to_view(record)

    def update_video_resource(
        self,
        *,
        owner_id: str,
        video_id: str,
        payload: VideoResourceUpdateRequest,
    ) -> VideoResourceView | None:
        record = self._repository.update_by_owner_and_id(
            owner_id=owner_id,
            video_id=video_id,
            file_name=payload.file_name,
            duration=payload.duration,
        )
        if record is None:
            return None
        return self._to_view(record)

    def delete_video_resource(self, *, owner_id: str, video_id: str) -> bool:
        return self._repository.delete_by_owner_and_id(owner_id, video_id)

    def _to_view(self, record: VideoResourceRecord) -> VideoResourceView:
        payload = asdict(record)
        keyframes = payload.get("keyframes")
        payload["keyframes"] = None if keyframes is None else [KeyFrameItem.model_validate(item) for item in keyframes]
        return VideoResourceView.model_validate(payload)
