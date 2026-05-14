from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from threading import Lock

try:
    from uuid import uuid7
except ImportError:  # pragma: no cover
    from uuid import uuid4 as uuid7


class KBVideoRelationRepository:
    def __init__(self) -> None:
        self._relations_by_owner: dict[str, dict[str, set[str]]] = defaultdict(dict)
        self._lock = Lock()

    def add_relation(self, *, owner_id: str, kbid: str, video_id: str) -> None:
        with self._lock:
            owner_bucket = self._relations_by_owner.setdefault(owner_id, {})
            video_ids = owner_bucket.setdefault(kbid, set())
            video_ids.add(video_id)

    def list_video_ids(self, *, owner_id: str, kbid: str) -> list[str]:
        with self._lock:
            owner_bucket = self._relations_by_owner.get(owner_id, {})
            video_ids = owner_bucket.get(kbid, set())
            return sorted(video_ids)

    def remove_relation(self, *, owner_id: str, kbid: str, video_id: str) -> None:
        with self._lock:
            owner_bucket = self._relations_by_owner.get(owner_id, {})
            video_ids = owner_bucket.get(kbid)
            if video_ids is None:
                return
            video_ids.discard(video_id)
            if not video_ids:
                owner_bucket.pop(kbid, None)

    def remove_all_by_kbid(self, *, owner_id: str, kbid: str) -> None:
        with self._lock:
            owner_bucket = self._relations_by_owner.get(owner_id, {})
            owner_bucket.pop(kbid, None)
