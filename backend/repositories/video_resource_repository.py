from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from threading import Lock

try:
    from uuid import uuid7
except ImportError:  # pragma: no cover
    from uuid import uuid4 as uuid7


@dataclass(frozen=True, slots=True)
class VideoResourceRecord:
    video_id: str
    owner_id: str
    file_name: str
    oss_key: str
    duration: int
    full_transcript: str | None
    transcribe_status: str
    transcript_vector_ids: list[str] | None
    keyframes: list[dict] | None
    frame_extraction_status: str
    keyframes_oss_prefix: str | None
    extract_completed_at: datetime | None
    created_at: datetime


class VideoResourceRepository:
    def __init__(self) -> None:
        self._records_by_owner: dict[str, dict[str, VideoResourceRecord]] = {}
        self._lock = Lock()

    def create(self, *, owner_id: str, file_name: str, oss_key: str, duration: int) -> VideoResourceRecord:
        record = VideoResourceRecord(
            video_id=str(uuid7()),
            owner_id=owner_id,
            file_name=file_name,
            oss_key=oss_key,
            duration=duration,
            full_transcript=None,
            transcribe_status="UPLOADED",
            transcript_vector_ids=None,
            keyframes=None,
            frame_extraction_status="UPLOADED",
            keyframes_oss_prefix=None,
            extract_completed_at=None,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            owner_bucket = self._records_by_owner.setdefault(owner_id, {})
            owner_bucket[record.video_id] = record
        return record

    def list_by_owner(self, owner_id: str) -> list[VideoResourceRecord]:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            return sorted(owner_bucket.values(), key=lambda item: item.created_at, reverse=True)

    def get_by_owner_and_id(self, owner_id: str, video_id: str) -> VideoResourceRecord | None:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            return owner_bucket.get(video_id)

    def update_by_owner_and_id(
        self,
        *,
        owner_id: str,
        video_id: str,
        file_name: str | None,
        duration: int | None,
    ) -> VideoResourceRecord | None:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            current = owner_bucket.get(video_id)
            if current is None:
                return None
            updated = replace(
                current,
                file_name=current.file_name if file_name is None else file_name,
                duration=current.duration if duration is None else duration,
            )
            owner_bucket[video_id] = updated
            return updated

    def delete_by_owner_and_id(self, owner_id: str, video_id: str) -> bool:
        with self._lock:
            owner_bucket = self._records_by_owner.get(owner_id, {})
            removed = owner_bucket.pop(video_id, None)
            return removed is not None
