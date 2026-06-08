from __future__ import annotations

import json
from typing import Any, Iterator

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.global_chat_repository import GlobalChatRepository
from backend.repositories.global_qa_repository import GlobalQARepository
from backend.schemas.global_chat import AttachmentInfo, CitedSource
from backend.schemas.global_qa import (
    GlobalQARecordCreateRequest,
    GlobalQARecordUpdateRequest,
    GlobalQARecordView,
)


class GlobalQAService:
    """全局跨文档问答服务（使用 QAAgent + ChatMemory 实现多轮对话记忆）。"""

    def __init__(
        self,
        repository: GlobalQARepository,
        chat_repository: GlobalChatRepository,
        qa_agent: Any,  # QAAgent (避免循环导入,使用 Any)
        chat_memory: Any = None,  # BaseChatMemory
    ) -> None:
        self._repository = repository
        self._chat_repository = chat_repository
        self._qa_agent = qa_agent
        self._chat_memory = chat_memory

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

        # Use QAAgent for answer (with conversation memory)
        answer_text = self._qa_agent.answer(
            question=payload.question_content,
            chat_id=chat_id,
            kbid=kbid,
            owner_id=owner_id,
        )
        cited_sources = getattr(self._qa_agent, "last_cited_sources", [])

        updated = self._repository.update_answer_by_owner_chat_and_qa_id(
            owner_id=owner_id,
            chat_id=chat_id,
            qa_id=record.qa_id,
            answer_content=answer_text,
            cited_sources=cited_sources,
        )

        # Record the turn in memory
        if self._chat_memory is not None:
            self._chat_memory.add_turn(
                chat_id=chat_id,
                question=payload.question_content,
                answer=answer_text,
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
        """创建问答记录并以流式返回回答 token。

        使用 QAAgent 的 answer_stream 进行 ReAct 循环，
        DB 写入在流耗尽后由生成器内部执行。
        """
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

        token_gen = self._qa_agent.answer_stream(
            question=payload.question_content,
            chat_id=chat_id,
            kbid=kbid,
            owner_id=owner_id,
        )
        accumulated: list[str] = []

        def _streaming_gen() -> Iterator[str]:
            for token in token_gen:
                accumulated.append(token)
                yield token
            # Collect cited sources from the agent
            cited_sources = getattr(self._qa_agent, "last_cited_sources", [])
            answer_text = "".join(accumulated)
            # Persist answer and sources to DB
            self._repository.update_answer_by_owner_chat_and_qa_id(
                owner_id=owner_id,
                chat_id=chat_id,
                qa_id=record.qa_id,
                answer_content=answer_text,
                cited_sources=cited_sources,
            )
            # Record the turn in memory (invalidates Redis cache)
            if self._chat_memory is not None:
                self._chat_memory.add_turn(
                    chat_id=chat_id,
                    question=payload.question_content,
                    answer=answer_text,
                )

        return self._to_qa_view(record), _streaming_gen()

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

        # Regenerate using QAAgent
        answer_text = self._qa_agent.answer(
            question=record.question_content,
            chat_id=chat_id,
            kbid=kbid,
            owner_id=owner_id,
        )
        cited_sources = getattr(self._qa_agent, "last_cited_sources", [])

        updated = self._repository.update_answer_by_owner_chat_and_qa_id(
            owner_id=owner_id,
            chat_id=chat_id,
            qa_id=qa_id,
            answer_content=answer_text,
            cited_sources=cited_sources,
        )
        if updated is None:
            return self._to_qa_view(record)
        return self._to_qa_view(updated)

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
        from backend.infrastructure.storage.oss_client import get_object_storage_client
        storage = get_object_storage_client()
        attachments = []
        cited_sources = []

        try:
            if record.attachments:
                attachments_data = json.loads(record.attachments)
                for a in attachments_data:
                    att = AttachmentInfo.model_validate(a)
                    try:
                        att = att.model_copy(update={"presigned_url": storage.get_presigned_url(object_key=att.oss_key)})
                    except Exception:
                        pass
                    attachments.append(att)
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
