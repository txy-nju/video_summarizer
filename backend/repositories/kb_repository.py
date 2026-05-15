from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from backend.models.database import KnowledgeBase, VideoResource, kb_video_relation_table


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
    def __init__(self, db_session: Session) -> None:
        self._session = db_session

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
        entity = KnowledgeBase(
            owner_id=owner_id,
            name=name,
            category=category,
            description=description,
            vector_collection_name=vector_collection_name,
            config=config,
        )
        self._session.add(entity)
        self._session.commit()
        self._session.refresh(entity)
        return self._to_record(entity)

    def list_by_owner(self, owner_id: str) -> list[KnowledgeBaseRecord]:
        rows = (
            self._session.query(KnowledgeBase)
            .filter(KnowledgeBase.owner_id == owner_id)
            .order_by(KnowledgeBase.kbid.desc())
            .all()
        )
        return [self._to_record(row) for row in rows]

    def get_by_owner_and_id(self, owner_id: str, kbid: str) -> KnowledgeBaseRecord | None:
        row = (
            self._session.query(KnowledgeBase)
            .filter(KnowledgeBase.owner_id == owner_id, KnowledgeBase.kbid == kbid)
            .one_or_none()
        )
        if row is None:
            return None
        return self._to_record(row)

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
        row = (
            self._session.query(KnowledgeBase)
            .filter(KnowledgeBase.owner_id == owner_id, KnowledgeBase.kbid == kbid)
            .one_or_none()
        )
        if row is None:
            return None

        if name is not None:
            row.name = name
        if category is not None:
            row.category = category
        if description is not None:
            row.description = description
        if config is not None:
            row.config = config

        self._session.commit()
        self._session.refresh(row)
        return self._to_record(row)

    def delete_by_owner_and_id(self, owner_id: str, kbid: str) -> bool:
        row = (
            self._session.query(KnowledgeBase)
            .filter(KnowledgeBase.owner_id == owner_id, KnowledgeBase.kbid == kbid)
            .one_or_none()
        )
        if row is None:
            return False

        self._session.delete(row)
        self._session.commit()
        return True

    @staticmethod
    def _to_record(entity: KnowledgeBase) -> KnowledgeBaseRecord:
        created_at = getattr(entity, "created_at", None) or datetime.now(UTC)
        return KnowledgeBaseRecord(
            kbid=str(entity.kbid),
            owner_id=str(entity.owner_id),
            name=entity.name,
            category=entity.category,
            description=entity.description,
            vector_collection_name=entity.vector_collection_name,
            config=entity.config or {},
            created_at=created_at,
        )

    def add_video_to_kb(self, owner_id: str, kbid: str, video_id: str) -> None:
        """
        Add a video to a knowledge base.
        - Ownership check: kbid belongs to owner_id
        - Idempotency: duplicate add returns success (no error)
        - Implementation: append video to kb.videos ORM relationship
        """
        kb = (
            self._session.query(KnowledgeBase)
            .filter(KnowledgeBase.owner_id == owner_id, KnowledgeBase.kbid == kbid)
            .one_or_none()
        )
        if kb is None:
            return

        video = (
            self._session.query(VideoResource)
            .filter(VideoResource.owner_id == owner_id, VideoResource.video_id == video_id)
            .one_or_none()
        )
        if video is None:
            return

        exists = self._session.execute(
            select(kb_video_relation_table.c.kbid).where(
                kb_video_relation_table.c.kbid == kbid,
                kb_video_relation_table.c.video_id == video_id,
            )
        ).first()
        if exists is not None:
            return

        self._session.execute(
            insert(kb_video_relation_table).values(kbid=kbid, video_id=video_id)
        )
        self._session.commit()

    def remove_video_from_kb(self, owner_id: str, kbid: str, video_id: str) -> None:
        """
        Remove a video from a knowledge base.
        - Ownership check: kbid belongs to owner_id
        - Idempotency: duplicate remove returns success (no error)
        - Implementation: remove video from kb.videos ORM relationship
        """
        kb = (
            self._session.query(KnowledgeBase)
            .filter(KnowledgeBase.owner_id == owner_id, KnowledgeBase.kbid == kbid)
            .one_or_none()
        )
        if kb is None:
            return

        video = (
            self._session.query(VideoResource)
            .filter(VideoResource.owner_id == owner_id, VideoResource.video_id == video_id)
            .one_or_none()
        )
        if video is None:
            return

        self._session.execute(
            delete(kb_video_relation_table).where(
                kb_video_relation_table.c.kbid == kbid,
                kb_video_relation_table.c.video_id == video_id,
            )
        )
        self._session.commit()

    def get_linked_video_ids(self, owner_id: str, kbid: str) -> list[str]:
        """
        Get all video IDs linked to a knowledge base.
        """
        kb = (
            self._session.query(KnowledgeBase)
            .filter(KnowledgeBase.owner_id == owner_id, KnowledgeBase.kbid == kbid)
            .one_or_none()
        )
        if kb is None:
            return []

        rows = self._session.execute(
            select(kb_video_relation_table.c.video_id).where(kb_video_relation_table.c.kbid == kbid)
        ).all()
        return [str(row[0]) for row in rows]
