from __future__ import annotations

from dataclasses import asdict
from uuid import uuid4

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.kb_repository import KnowledgeBaseRecord, KnowledgeBaseRepository
from backend.repositories.video_resource_repository import VideoResourceRecord, VideoResourceRepository
from backend.schemas.kb import KnowledgeBaseConfig, KnowledgeBaseCreateRequest, KnowledgeBaseUpdateRequest, KnowledgeBaseView
from backend.schemas.kb import KnowledgeBaseVideoItem


class KnowledgeBaseService:
    def __init__(
        self,
        repository: KnowledgeBaseRepository,
        video_repository: VideoResourceRepository,
    ) -> None:
        self._repository = repository
        self._video_repository = video_repository

    def create_knowledge_base(self, *, owner_id: str, payload: KnowledgeBaseCreateRequest) -> KnowledgeBaseView:
        record = self._repository.create(
            owner_id=owner_id,
            name=payload.name,
            category=payload.category,
            description=payload.description,
            vector_collection_name=f"kb_{uuid4().hex}",
            config=payload.config.model_dump(),
        )
        return self._to_view(record)

    def list_knowledge_bases(self, *, owner_id: str, page: int, page_size: int) -> tuple[list[KnowledgeBaseView], dict]:
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

    def get_knowledge_base(self, *, owner_id: str, kbid: str) -> KnowledgeBaseView | None:
        record = self._repository.get_by_owner_and_id(owner_id, kbid)
        if record is None:
            return None
        return self._to_view(record)

    def update_knowledge_base(
        self,
        *,
        owner_id: str,
        kbid: str,
        payload: KnowledgeBaseUpdateRequest,
    ) -> KnowledgeBaseView | None:
        record = self._repository.update_by_owner_and_id(
            owner_id=owner_id,
            kbid=kbid,
            name=payload.name,
            category=payload.category,
            description=payload.description,
            config=None if payload.config is None else payload.config.model_dump(),
        )
        if record is None:
            return None
        return self._to_view(record)

    def delete_knowledge_base(self, *, owner_id: str, kbid: str) -> bool:
        # Cascade delete of kb_video_relations is handled by database ON DELETE CASCADE
        deleted = self._repository.delete_by_owner_and_id(owner_id, kbid)
        return deleted

    def add_video_to_knowledge_base(self, *, owner_id: str, kbid: str, video_id: str) -> bool:
        kb = self._repository.get_by_owner_and_id(owner_id, kbid)
        video = self._video_repository.get_by_owner_and_id(owner_id, video_id)
        if kb is None or video is None:
            return False

        # Use new Repository method that operates on ORM relationship
        self._repository.add_video_to_kb(owner_id=owner_id, kbid=kbid, video_id=video_id)
        return True

    def list_knowledge_base_videos(self, *, owner_id: str, kbid: str, page: int, page_size: int) -> tuple[list[KnowledgeBaseVideoItem], dict] | None:
        kb = self._repository.get_by_owner_and_id(owner_id, kbid)
        if kb is None:
            return None

        linked_video_ids = set(self._repository.get_linked_video_ids(owner_id, kbid))
        all_videos = self._video_repository.list_by_owner(owner_id)
        linked_videos = [video for video in all_videos if video.video_id in linked_video_ids]

        normalized_page_size = normalize_page_size(page_size)
        start_index = max(page - 1, 0) * normalized_page_size
        end_index = start_index + normalized_page_size
        page_items = linked_videos[start_index:end_index]

        views = [self._to_video_item(video) for video in page_items]
        pagination = build_pagination(
            page=page,
            page_size=normalized_page_size,
            total=len(linked_videos),
            next_cursor=None,
        )
        return views, pagination

    def remove_video_from_knowledge_base(self, *, owner_id: str, kbid: str, video_id: str) -> bool:
        kb = self._repository.get_by_owner_and_id(owner_id, kbid)
        video = self._video_repository.get_by_owner_and_id(owner_id, video_id)
        if kb is None or video is None:
            return False

        # Delete semantics are idempotent; missing relation is treated as no-op success.
        self._repository.remove_video_from_kb(owner_id=owner_id, kbid=kbid, video_id=video_id)
        return True

    def _to_view(self, record: KnowledgeBaseRecord) -> KnowledgeBaseView:
        return KnowledgeBaseView.model_validate({**asdict(record), "config": KnowledgeBaseConfig.model_validate(record.config)})

    def _to_video_item(self, record: VideoResourceRecord) -> KnowledgeBaseVideoItem:
        return KnowledgeBaseVideoItem(
            video_id=record.video_id,
            file_name=record.file_name,
            created_at=record.created_at,
        )
