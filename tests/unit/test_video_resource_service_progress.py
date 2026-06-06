from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from backend.repositories.video_resource_repository import VideoResourceRecord
from backend.services.video_resource_service import VideoResourceService


class _FakeVideoRepo:
    def __init__(self, seed: VideoResourceRecord) -> None:
        self.record = seed

    def get_by_id_system(self, video_id: str) -> VideoResourceRecord | None:
        if video_id != self.record.video_id:
            return None
        return self.record

    def update_transcription_status(self, video_id: str, status, *, full_transcript: str | None = None) -> None:
        if video_id != self.record.video_id:
            return
        self.record = replace(
            self.record,
            transcribe_status=str(getattr(status, "value", status)),
            full_transcript=full_transcript if full_transcript is not None else self.record.full_transcript,
        )

    def update_frame_extraction(self, video_id: str, status, *, keyframes=None, keyframes_oss_prefix=None) -> None:
        if video_id != self.record.video_id:
            return
        self.record = replace(
            self.record,
            frame_extraction_status=str(getattr(status, "value", status)),
            keyframes=keyframes if keyframes is not None else self.record.keyframes,
            keyframes_oss_prefix=keyframes_oss_prefix if keyframes_oss_prefix is not None else self.record.keyframes_oss_prefix,
        )

    def update_extract_completed_at(self, video_id: str) -> None:
        if video_id != self.record.video_id:
            return
        if self.record.transcribe_status == "COMPLETED" and self.record.frame_extraction_status == "COMPLETED":
            self.record = replace(self.record, extract_completed_at=datetime.now(UTC))


def _seed_video() -> VideoResourceRecord:
    return VideoResourceRecord(
        video_id="vid-1",
        owner_id="user-1",
        file_name="v.mp4",
        oss_key="videos/user-1/vid-1/original.mp4",
        duration=120,
        full_transcript=None,
        transcript_segments=None,
        transcribe_status="UPLOADED",
        transcript_vector_ids=None,
        keyframes=None,
        frame_extraction_status="UPLOADED",
        keyframes_oss_prefix=None,
        extract_completed_at=None,
        is_deleted=False,
        deleted_at=None,
        deletion_status="NONE",
        created_at=datetime.now(UTC),
    )


def test_mark_transcription_in_progress_updates_status() -> None:
    """验证 mark_transcription_in_progress 将转录状态更新为 TRANSCRIBING。"""
    repo = _FakeVideoRepo(_seed_video())
    service = VideoResourceService(repository=repo)

    service.mark_transcription_in_progress(video_id="vid-1")

    assert repo.record.transcribe_status == "TRANSCRIBING"


def test_mark_transcription_in_progress_noop_on_missing_video() -> None:
    """验证对不存在的视频 ID 调用时不会抛异常。"""
    repo = _FakeVideoRepo(_seed_video())
    service = VideoResourceService(repository=repo)

    service.mark_transcription_in_progress(video_id="vid-nonexistent")

    assert repo.record.transcribe_status == "UPLOADED"  # unchanged


def test_mark_extract_completed_if_ready_requires_dual_completed() -> None:
    """验证只有在转录和关键帧都完成后才设置 extract_completed_at。"""
    repo = _FakeVideoRepo(_seed_video())
    service = VideoResourceService(repository=repo)

    assert service.mark_extract_completed_if_ready(video_id="vid-1") is False

    repo.record = replace(
        repo.record,
        transcribe_status="COMPLETED",
        frame_extraction_status="COMPLETED",
    )

    assert service.mark_extract_completed_if_ready(video_id="vid-1") is True
    assert repo.record.extract_completed_at is not None
