from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock

try:
    from uuid import uuid7
except ImportError:  # pragma: no cover
    from uuid import uuid4 as uuid7


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
    """全局问答 Repository（内存实现，步骤 4 临时方案）"""

    def __init__(self) -> None:
        self._records_by_owner: dict[str, dict[str, dict[str, GlobalQARecordData]]] = {}
        self._lock = Lock()

    def create(
        self,
        *,
        owner_id: str,
        chat_id: str,
        question_content: str,
        attachments: list[dict],
    ) -> GlobalQARecordData:
        now = datetime.now(UTC)
        record = GlobalQARecordData(
            qa_id=str(uuid7()),
            chat_id=chat_id,
            owner_id=owner_id,
            question_content=question_content,
            answer_content=None,
            attachments=json.dumps(attachments),
            cited_sources=json.dumps([]),
            question_time=now,
        )
        with self._lock:
            owner_bucket = self._records_by_owner.setdefault(owner_id, {})
            chat_bucket = owner_bucket.setdefault(chat_id, {})
            chat_bucket[record.qa_id] = record
        return record

    def list_by_owner_and_chat(self, owner_id: str, chat_id: str) -> list[GlobalQARecordData]:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            chat_bucket = owner_bucket.get(chat_id, {})
            return sorted(chat_bucket.values(), key=lambda item: item.question_time)

    def get_by_owner_chat_and_qa_id(
        self, owner_id: str, chat_id: str, qa_id: str
    ) -> GlobalQARecordData | None:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            chat_bucket = owner_bucket.get(chat_id, {})
            return chat_bucket.get(qa_id)

    def update_answer_by_owner_chat_and_qa_id(
        self,
        owner_id: str,
        chat_id: str,
        qa_id: str,
        answer_content: str,
        cited_sources: list[dict] | None = None,
    ) -> GlobalQARecordData | None:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            chat_bucket = owner_bucket.get(chat_id, {})
            current = chat_bucket.get(qa_id)
            if current is None:
                return None
            updated = replace(
                current,
                answer_content=answer_content,
                cited_sources=json.dumps(cited_sources or []),
            )
            chat_bucket[qa_id] = updated
            return updated

    def delete_by_owner_chat_and_qa_id(self, owner_id: str, chat_id: str, qa_id: str) -> bool:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            chat_bucket = owner_bucket.get(chat_id, {})
            if qa_id in chat_bucket:
                del chat_bucket[qa_id]
                return True
        return False

    def delete_all_by_owner_and_chat(self, owner_id: str, chat_id: str) -> int:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            chat_bucket = owner_bucket.get(chat_id, {})
            count = len(chat_bucket)
            chat_bucket.clear()
        return count
