from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.models.database import GlobalChatSession, GlobalQARecord, KnowledgeBase


@dataclass(frozen=True, slots=True)
class GlobalQARecordData:
    """全局跨文档问答记录"""

    qa_id: str
    chat_id: str
    owner_id: str
    question_content: str
    answer_content: str | None
    attachments: str
    cited_sources: str
    question_time: datetime


class GlobalQARepository:
    """全局问答 Repository（数据库实现）"""

    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def create(
        self,
        *,
        owner_id: str,
        chat_id: str,
        question_content: str,
        attachments: list[dict],
    ) -> GlobalQARecordData:
        entity = GlobalQARecord(
            chat_id=chat_id,
            question_content=question_content,
            answer_content=None,
            attachments=attachments,
            cited_sources=[],
        )
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return self._to_record(entity, owner_id=owner_id)

    def list_by_owner_and_chat(self, owner_id: str, chat_id: str) -> list[GlobalQARecordData]:
        rows = (
            self._session.query(GlobalQARecord)
            .join(GlobalChatSession, GlobalQARecord.chat_id == GlobalChatSession.chat_id)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(KnowledgeBase.owner_id == owner_id, GlobalQARecord.chat_id == chat_id)
            .order_by(GlobalQARecord.question_time.asc())
            .all()
        )
        return [self._to_record(row, owner_id=owner_id) for row in rows]

    def get_by_owner_chat_and_qa_id(
        self, owner_id: str, chat_id: str, qa_id: str
    ) -> GlobalQARecordData | None:
        row = (
            self._session.query(GlobalQARecord)
            .join(GlobalChatSession, GlobalQARecord.chat_id == GlobalChatSession.chat_id)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                GlobalQARecord.chat_id == chat_id,
                GlobalQARecord.qa_id == qa_id,
            )
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_record(row, owner_id=owner_id)

    def update_answer_by_owner_chat_and_qa_id(
        self,
        owner_id: str,
        chat_id: str,
        qa_id: str,
        answer_content: str,
        cited_sources: list[dict] | None = None,
    ) -> GlobalQARecordData | None:
        row = (
            self._session.query(GlobalQARecord)
            .join(GlobalChatSession, GlobalQARecord.chat_id == GlobalChatSession.chat_id)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                GlobalQARecord.chat_id == chat_id,
                GlobalQARecord.qa_id == qa_id,
            )
            .one_or_none()
        )
        if row is None:
            return None

        row.answer_content = answer_content
        row.cited_sources = cited_sources or []
        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row, owner_id=owner_id)

    def delete_by_owner_chat_and_qa_id(self, owner_id: str, chat_id: str, qa_id: str) -> bool:
        row = (
            self._session.query(GlobalQARecord)
            .join(GlobalChatSession, GlobalQARecord.chat_id == GlobalChatSession.chat_id)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                GlobalQARecord.chat_id == chat_id,
                GlobalQARecord.qa_id == qa_id,
            )
            .one_or_none()
        )
        if row is None:
            return False

        self._session.delete(row)
        self._session.commit()
        return True

    def delete_all_by_owner_and_chat(self, owner_id: str, chat_id: str) -> int:
        rows = (
            self._session.query(GlobalQARecord)
            .join(GlobalChatSession, GlobalQARecord.chat_id == GlobalChatSession.chat_id)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(KnowledgeBase.owner_id == owner_id, GlobalQARecord.chat_id == chat_id)
            .all()
        )
        count = len(rows)
        for row in rows:
            self._session.delete(row)
        if count > 0:
            self._session.commit()
        return count

    @staticmethod
    def _to_record(entity: GlobalQARecord, *, owner_id: str) -> GlobalQARecordData:
        question_time = getattr(entity, "question_time", None) or datetime.now(UTC)
        attachments = entity.attachments or []
        cited_sources = entity.cited_sources or []
        return GlobalQARecordData(
            qa_id=str(entity.qa_id),
            chat_id=str(entity.chat_id),
            owner_id=owner_id,
            question_content=entity.question_content,
            answer_content=entity.answer_content,
            attachments=json.dumps(attachments),
            cited_sources=json.dumps(cited_sources),
            question_time=question_time,
        )
