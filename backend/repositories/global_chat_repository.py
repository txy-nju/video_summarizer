from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from backend.models.database import GlobalChatSession, KnowledgeBase


@dataclass(frozen=True, slots=True)
class GlobalChatSessionData:
    """全局知识库会话"""
    chat_id: str
    kbid: str
    owner_id: str  # 从 kb 继承获取，用于权限校验
    chat_title: str
    created_at: datetime


class GlobalChatRepository:
    """全局会话 Repository（数据库实现）"""

    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def create(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_title: str | None = None,
    ) -> GlobalChatSessionData:
        now = datetime.now(UTC)
        title = chat_title or f"Chat {now.strftime('%Y-%m-%d %H:%M:%S')}"
        entity = GlobalChatSession(
            kbid=kbid,
            chat_title=title,
        )
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return self._to_record(entity, owner_id=owner_id)

    def list_by_owner_and_kb(self, owner_id: str, kbid: str) -> list[GlobalChatSessionData]:
        rows = (
            self._session.query(GlobalChatSession)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(KnowledgeBase.owner_id == owner_id, GlobalChatSession.kbid == kbid)
            .order_by(GlobalChatSession.created_at.desc())
            .all()
        )
        return [self._to_record(row, owner_id=owner_id) for row in rows]

    def get_by_owner_kb_and_chat_id(
        self, owner_id: str, kbid: str, chat_id: str
    ) -> GlobalChatSessionData | None:
        row = (
            self._session.query(GlobalChatSession)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                GlobalChatSession.kbid == kbid,
                GlobalChatSession.chat_id == chat_id,
            )
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_record(row, owner_id=owner_id)

    def update_title_by_owner_kb_and_chat_id(
        self, owner_id: str, kbid: str, chat_id: str, chat_title: str
    ) -> GlobalChatSessionData | None:
        row = (
            self._session.query(GlobalChatSession)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                GlobalChatSession.kbid == kbid,
                GlobalChatSession.chat_id == chat_id,
            )
            .one_or_none()
        )
        if row is None:
            return None

        row.chat_title = chat_title
        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row, owner_id=owner_id)

    def delete_by_owner_kb_and_chat_id(
        self, owner_id: str, kbid: str, chat_id: str
    ) -> bool:
        row = (
            self._session.query(GlobalChatSession)
            .join(KnowledgeBase, GlobalChatSession.kbid == KnowledgeBase.kbid)
            .filter(
                KnowledgeBase.owner_id == owner_id,
                GlobalChatSession.kbid == kbid,
                GlobalChatSession.chat_id == chat_id,
            )
            .one_or_none()
        )
        if row is None:
            return False

        self._session.delete(row)
        self._session.commit()
        return True

    @staticmethod
    def _to_record(entity: GlobalChatSession, *, owner_id: str) -> GlobalChatSessionData:
        created_at = getattr(entity, "created_at", None) or datetime.now(UTC)
        return GlobalChatSessionData(
            chat_id=str(entity.chat_id),
            kbid=str(entity.kbid),
            owner_id=owner_id,
            chat_title=entity.chat_title or "",
            created_at=created_at,
        )
