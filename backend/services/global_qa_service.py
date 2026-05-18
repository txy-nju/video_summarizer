from __future__ import annotations

import json
from typing import Iterator

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.global_chat_repository import GlobalChatRepository
from backend.repositories.global_qa_repository import GlobalQARepository
from backend.schemas.global_chat import AttachmentInfo, CitedSource
from backend.schemas.global_qa import (
    GlobalQARecordCreateRequest,
    GlobalQARecordUpdateRequest,
    GlobalQARecordView,
)
from backend.services.rag_agent_service import RagAgentService


class GlobalQAService:
    """全局跨文档问答服务"""

    def __init__(
        self,
        repository: GlobalQARepository,
        chat_repository: GlobalChatRepository,
        rag_agent_service: RagAgentService,
    ) -> None:
        self._repository = repository
        self._chat_repository = chat_repository
        self._rag_agent_service = rag_agent_service

    def create_qa_record(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
        payload: GlobalQARecordCreateRequest,
    ) -> GlobalQARecordView | None:
        chat = self._chat_repository.get_by_owner_kb_and_chat_id(owner_id, kbid, chat_id)
        if chat is None:
            return None

        attachments_data = [asdict(a) for a in payload.attachments]
        record = self._repository.create(
            owner_id=owner_id,
            chat_id=chat_id,
            question_content=payload.question_content,
            attachments=attachments_data,
        )

        rag_answer = self._rag_agent_service.answer_global_question(
            owner_id=owner_id,
            kbid=kbid,
            question_content=payload.question_content,
            attachments=attachments_data,
        )
        updated = self._repository.update_answer_by_owner_chat_and_qa_id(
            owner_id=owner_id,
            chat_id=chat_id,
            qa_id=record.qa_id,
            answer_content=rag_answer.answer_content,
            cited_sources=rag_answer.cited_sources,
        )
        if updated is None:
            return self._to_qa_view(record)
        return self._to_qa_view(updated)

    def create_qa_record_stream(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
        payload: GlobalQARecordCreateRequest,
    ) -> tuple[GlobalQARecordView, Iterator[str]] | tuple[None, None]:
        """创建问答记录并返回流式回答块迭代器。"""
        chat = self._chat_repository.get_by_owner_kb_and_chat_id(owner_id, kbid, chat_id)
        if chat is None:
            return None, None

        attachments_data = [asdict(a) for a in payload.attachments]
        record = self._repository.create(
            owner_id=owner_id,
            chat_id=chat_id,
            question_content=payload.question_content,
            attachments=attachments_data,
        )

        rag_answer, chunks = self._rag_agent_service.stream_global_question(
            owner_id=owner_id,
            kbid=kbid,
            question_content=payload.question_content,
            attachments=attachments_data,
        )
        updated = self._repository.update_answer_by_owner_chat_and_qa_id(
            owner_id=owner_id,
            chat_id=chat_id,
            qa_id=record.qa_id,
            answer_content=rag_answer.answer_content,
            cited_sources=rag_answer.cited_sources,
        )
        view = self._to_qa_view(updated if updated is not None else record)
        return view, chunks

    def list_qa_records(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
        page: int,
        page_size: int,
    ) -> tuple[list[GlobalQARecordView], dict]:
        chat = self._chat_repository.get_by_owner_kb_and_chat_id(owner_id, kbid, chat_id)
        if chat is None:
            return [], build_pagination(page=page, page_size=page_size, total=0)

        records = self._repository.list_by_owner_and_chat(owner_id, chat_id)
        normalized_page_size = normalize_page_size(page_size)
        start_index = max(page - 1, 0) * normalized_page_size
        end_index = start_index + normalized_page_size
        page_items = records[start_index:end_index]

        views = [self._to_qa_view(record) for record in page_items]
        pagination = build_pagination(
            page=page,
            page_size=normalized_page_size,
            total=len(records),
            next_cursor=None,
        )
        return views, pagination

    def get_qa_record(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
        qa_id: str,
    ) -> GlobalQARecordView | None:
        chat = self._chat_repository.get_by_owner_kb_and_chat_id(owner_id, kbid, chat_id)
        if chat is None:
            return None

        record = self._repository.get_by_owner_chat_and_qa_id(owner_id, chat_id, qa_id)
        if record is None:
            return None
        return self._to_qa_view(record)

    def update_qa_record(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
        qa_id: str,
        payload: GlobalQARecordUpdateRequest,
    ) -> GlobalQARecordView | None:
        chat = self._chat_repository.get_by_owner_kb_and_chat_id(owner_id, kbid, chat_id)
        if chat is None:
            return None

        if not payload.regenerate:
            return None

        record = self._repository.get_by_owner_chat_and_qa_id(owner_id, chat_id, qa_id)
        if record is None:
            return None

        return self._to_qa_view(record)

    def delete_qa_record(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_id: str,
        qa_id: str,
    ) -> bool:
        chat = self._chat_repository.get_by_owner_kb_and_chat_id(owner_id, kbid, chat_id)
        if chat is None:
            return False

        return self._repository.delete_by_owner_chat_and_qa_id(owner_id, chat_id, qa_id)

    @staticmethod
    def _to_qa_view(record) -> GlobalQARecordView:
        attachments = []
        cited_sources = []

        try:
            if record.attachments:
                attachments_data = json.loads(record.attachments)
                attachments = [AttachmentInfo(**a) for a in attachments_data]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        try:
            if record.cited_sources:
                cited_sources_data = json.loads(record.cited_sources)
                cited_sources = [CitedSource(**s) for s in cited_sources_data]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        return GlobalQARecordView(
            qa_id=str(record.qa_id),
            chat_id=str(record.chat_id),
            question_content=record.question_content,
            answer_content=record.answer_content,
            attachments=attachments,
            cited_sources=cited_sources,
            question_time=record.question_time,
        )


def asdict(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return {}
