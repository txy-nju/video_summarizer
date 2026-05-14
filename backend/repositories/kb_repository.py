from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock

try:
    from uuid import uuid7
except ImportError:  # pragma: no cover - Python versions without uuid7
    from uuid import uuid4 as uuid7


@dataclass(frozen=True, slots=True)
class KnowledgeBaseRecord:
    kbid: str
    owner_id: str
    name: str
    category: str | None
    description: str | None
    vector_collection_name: str | None
    config: dict
    created_at: datetime


class KnowledgeBaseRepository:
    def __init__(self) -> None:
        self._records_by_owner: dict[str, dict[str, KnowledgeBaseRecord]] = {}
        self._lock = Lock()

    def create(
        self,
        *,
        owner_id: str,
        name: str,
        category: str | None,
        description: str | None,
        vector_collection_name: str | None,
        config: dict,
    ) -> KnowledgeBaseRecord:
        record = KnowledgeBaseRecord(
            kbid=str(uuid7()),
            owner_id=owner_id,
            name=name,
            category=category,
            description=description,
            vector_collection_name=vector_collection_name,
            config=config,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            owner_bucket = self._records_by_owner.setdefault(owner_id, {})
            owner_bucket[record.kbid] = record
        return record

    def list_by_owner(self, owner_id: str) -> list[KnowledgeBaseRecord]:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            return sorted(owner_bucket.values(), key=lambda item: item.created_at, reverse=True)

    def get_by_owner_and_id(self, owner_id: str, kbid: str) -> KnowledgeBaseRecord | None:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            return owner_bucket.get(kbid)

    def update_by_owner_and_id(
        self,
        *,
        owner_id: str,
        kbid: str,
        name: str | None,
        category: str | None,
        description: str | None,
        config: dict | None,
    ) -> KnowledgeBaseRecord | None:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            current = owner_bucket.get(kbid)
            if current is None:
                return None

            updated = replace(
                current,
                name=current.name if name is None else name,
                category=current.category if category is None else category,
                description=current.description if description is None else description,
                config=current.config if config is None else config,
            )
            owner_bucket[kbid] = updated
            return updated

    def delete_by_owner_and_id(self, owner_id: str, kbid: str) -> bool:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            removed = owner_bucket.pop(kbid, None)
            return removed is not None
