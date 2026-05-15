from __future__ import annotations

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.global_chat_repository import GlobalChatRepository
from backend.repositories.global_qa_repository import GlobalQARepository
from backend.repositories.kb_repository import KnowledgeBaseRepository
from backend.schemas.global_chat import (
    GlobalChatSessionCreateRequest,
    GlobalChatSessionUpdateRequest,
    GlobalChatSessionView,
)


class GlobalChatService:
    """全局知识库会话服务"""

    def __init__(
        self,
        repository: GlobalChatRepository,
        kb_repository: KnowledgeBaseRepository,
        qa_repository: GlobalQARepository,
    ) -> None:
        self._repository = repository
        self._kb_repository = kb_repository
        self._qa_repository = qa_repository

    def create_chat_session(
        self,
        *,
        owner_id: str,
        payload: GlobalChatSessionCreateRequest,
    ) -> GlobalChatSessionView | None:
        """创建新的全局会话"""
        # 验证知识库是否属于该用户
        kb = self._kb_repository.get_by_owner_and_id(owner_id, payload.kbid)
        if kb is None:
            return None

        record = self._repository.create(
            owner_id=owner_id,
            kbid=payload.kbid,
            chat_title=payload.chat_title,
        )
        return self._to_chat_view(record)

    def list_chat_sessions(
        self,
        *,
        owner_id: str,
        kbid: str,
        page: int,
        page_size: int,
    ) -> tuple[list[GlobalChatSessionView], dict]:
        """查询某个知识库下的所有会话"""
        # 验证知识库是否属于该用户
        kb = self._kb_repository.get_by_owner_and_id(owner_id, kbid)
        if kb is None:
            return [], build_pagination(page=page, page_size=page_size, total=0)

        records = self._repository.list_by_owner_and_kb(owner_id, kbid)
        normalized_page_size = normalize_page_size(page_size)
        start_index = max(page - 1, 0) * normalized_page_size
        end_index = start_index + normalized_page_size
        page_items = records[start_index:end_index]

        views = [self._to_chat_view(record) for record in page_items]
        pagination = build_pagination(
            page=page,
            page_size=normalized_page_size,
            total=len(records),
            next_cursor=None,
        )
        return views, pagination

    def get_chat_session(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
    ) -> GlobalChatSessionView | None:
        """获取单条会话记录"""
        record = self._repository.get_by_owner_kb_and_chat_id(owner_id, kbid, chat_id)
        if record is None:
            return None
        return self._to_chat_view(record)

    def update_chat_session(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
        payload: GlobalChatSessionUpdateRequest,
    ) -> GlobalChatSessionView | None:
        """更新会话标题"""
        record = self._repository.update_title_by_owner_kb_and_chat_id(
            owner_id, kbid, chat_id, payload.chat_title
        )
        if record is None:
            return None
        return self._to_chat_view(record)

    def delete_chat_session(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
    ) -> bool:
        """删除会话及其所有问答"""
        # 先删除该会话下的所有问答
        self._qa_repository.delete_all_by_owner_and_chat(owner_id, chat_id)
        # 再删除会话本身
        return self._repository.delete_by_owner_kb_and_chat_id(owner_id, kbid, chat_id)

    @staticmethod
    def _to_chat_view(record) -> GlobalChatSessionView:
        """将 Repository 记录转换为视图"""
        return GlobalChatSessionView(
            chat_id=str(record.chat_id),
            kbid=str(record.kbid),
            chat_title=record.chat_title,
            created_at=record.created_at,
        )
