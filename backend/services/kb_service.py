from __future__ import annotations

from dataclasses import asdict

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.kb_repository import KnowledgeBaseRecord, KnowledgeBaseRepository
from backend.schemas.kb import KnowledgeBaseConfig, KnowledgeBaseCreateRequest, KnowledgeBaseUpdateRequest, KnowledgeBaseView


class KnowledgeBaseService:
    def __init__(self, repository: KnowledgeBaseRepository) -> None:
        self._repository = repository

    def create_knowledge_base(self, *, owner_id: str, payload: KnowledgeBaseCreateRequest) -> KnowledgeBaseView:
        record = self._repository.create(
            owner_id=owner_id,
            name=payload.name,
            category=payload.category,
            description=payload.description,
            vector_collection_name=None,
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
        return self._repository.delete_by_owner_and_id(owner_id, kbid)

    def _to_view(self, record: KnowledgeBaseRecord) -> KnowledgeBaseView:
        return KnowledgeBaseView.model_validate({**asdict(record), "config": KnowledgeBaseConfig.model_validate(record.config)})
