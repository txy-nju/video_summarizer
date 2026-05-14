from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock

try:
    from uuid import uuid7
except ImportError:  # pragma: no cover
    from uuid import uuid4 as uuid7


@dataclass(frozen=True, slots=True)
class GlobalChatSessionData:
    """全局知识库会话"""
    chat_id: str
    kbid: str
    owner_id: str  # 从 kb 继承获取，用于权限校验
    chat_title: str
    created_at: datetime


class GlobalChatRepository:
    """全局会话 Repository（内存实现，步骤 4 临时方案）"""

    def __init__(self) -> None:
        # 按 owner_id -> kb_id -> chat_id 三层嵌套存储
        self._sessions_by_owner: dict[str, dict[str, dict[str, GlobalChatSessionData]]] = {}
        self._lock = Lock()

    def create(
        self,
        *,
        owner_id: str,
        kbid: str,
        chat_title: str | None = None,
    ) -> GlobalChatSessionData:
        """创建新的全局会话"""
        now = datetime.now(UTC)
        # 若未提供标题，使用默认标题，后续用户可更新
        title = chat_title or f"Chat {now.strftime('%Y-%m-%d %H:%M:%S')}"
        record = GlobalChatSessionData(
            chat_id=str(uuid7()),
            kbid=kbid,
            owner_id=owner_id,
            chat_title=title,
            created_at=now,
        )
        with self._lock:
            owner_bucket = self._sessions_by_owner.setdefault(owner_id, {})
            kb_bucket = owner_bucket.setdefault(kbid, {})
            kb_bucket[record.chat_id] = record
        return record

    def list_by_owner_and_kb(self, owner_id: str, kbid: str) -> list[GlobalChatSessionData]:
        """查询某个知识库下的所有会话"""
        with self._lock:
            owner_bucket = self._sessions_by_owner.get(owner_id, {})
            kb_bucket = owner_bucket.get(kbid, {})
            return sorted(kb_bucket.values(), key=lambda item: item.created_at, reverse=True)

    def get_by_owner_kb_and_chat_id(
        self, owner_id: str, kbid: str, chat_id: str
    ) -> GlobalChatSessionData | None:
        """获取单条会话记录"""
        with self._lock:
            owner_bucket = self._sessions_by_owner.get(owner_id, {})
            kb_bucket = owner_bucket.get(kbid, {})
            return kb_bucket.get(chat_id)

    def update_title_by_owner_kb_and_chat_id(
        self, owner_id: str, kbid: str, chat_id: str, chat_title: str
    ) -> GlobalChatSessionData | None:
        """更新会话标题"""
        with self._lock:
            owner_bucket = self._sessions_by_owner.get(owner_id, {})
            kb_bucket = owner_bucket.get(kbid, {})
            current = kb_bucket.get(chat_id)
            if current is None:
                return None
            updated = replace(current, chat_title=chat_title)
            kb_bucket[chat_id] = updated
            return updated

    def delete_by_owner_kb_and_chat_id(
        self, owner_id: str, kbid: str, chat_id: str
    ) -> bool:
        """删除单条会话记录"""
        with self._lock:
            owner_bucket = self._sessions_by_owner.get(owner_id, {})
            kb_bucket = owner_bucket.get(kbid, {})
            if chat_id in kb_bucket:
                del kb_bucket[chat_id]
                return True
        return False
