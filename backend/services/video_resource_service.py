from __future__ import annotations

from dataclasses import asdict
import logging

from backend.api.pagination import build_pagination, normalize_page_size
from backend.models.enums import FrameExtractionStatus, TranscribeStatus
from backend.repositories.video_resource_repository import VideoResourceRecord, VideoResourceRepository
from backend.schemas.video_resource import KeyFrameItem, VideoResourceCreateRequest, VideoResourceUpdateRequest, VideoResourceView


logger = logging.getLogger(__name__)


def _dispatch_async_cascade_delete(video_id: str) -> None:
    from backend.tasks.video_cleanup_tasks import async_cascade_delete_video

    async_cascade_delete_video.delay(video_id)


def _dispatch_async_process_video(video_id: str) -> None:
    from backend.tasks.video_summary_tasks import async_process_video

    async_process_video.delay(video_id)


class VideoResourceService:
    def __init__(self, repository: VideoResourceRepository) -> None:
        self._repository = repository

    def create_video_resource(self, *, owner_id: str, payload: VideoResourceCreateRequest) -> VideoResourceView:
        record = self._repository.create(
            owner_id=owner_id,
            file_name=payload.file_name,
        )
        return self._to_view(record)

    def list_video_resources(self, *, owner_id: str, page: int, page_size: int) -> tuple[list[VideoResourceView], dict]:
        records = self._repository.list_by_owner(owner_id)
        normalized_page_size = normalize_page_size(page_size)
        start_index = max(page - 1, 0) * normalized_page_size
        end_index = start_index + normalized_page_size
        page_items = records[start_index:end_index]

        views = [self._to_view(record) for record in page_items]
        pagination = build_pagination(
            page=page,
            page_size=normalized_page_size,
            total=len(records),
            next_cursor=None,
        )
        return views, pagination

    def get_video_resource(self, *, owner_id: str, video_id: str) -> VideoResourceView | None:
        record = self._repository.get_by_owner_and_id(owner_id, video_id)
        if record is None:
            return None
        return self._to_view(record)

    def update_video_resource(
        self,
        *,
        owner_id: str,
        video_id: str,
        payload: VideoResourceUpdateRequest,
    ) -> VideoResourceView | None:
        record = self._repository.update_by_owner_and_id(
            owner_id=owner_id,
            video_id=video_id,
            file_name=payload.file_name,
        )
        if record is None:
            return None
        return self._to_view(record)

    def delete_video_resource(self, *, owner_id: str, video_id: str) -> bool:
        deleted = self._repository.delete_by_owner_and_id(owner_id, video_id)
        if not deleted:
            return False

        # API 线程只做软删除受理；跨存储清理由异步任务执行。
        try:
            _dispatch_async_cascade_delete(video_id)
        except Exception as exc:
            logger.warning(
                "Failed to dispatch async_cascade_delete_video for video_id=%s: %s",
                video_id,
                exc,
            )

        return True

    def trigger_processing_after_upload(self, *, video_id: str) -> bool:
        """System-only hook: trigger async extraction pipeline after upload is finalized."""
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return False
        if video.is_deleted:
            return False
        if not (video.oss_key and video.oss_key.strip()):
            return False

        try:
            _dispatch_async_process_video(video_id)
            return True
        except Exception as exc:
            logger.warning(
                "Failed to dispatch async_process_video for video_id=%s: %s",
                video_id,
                exc,
            )
            return False

    def get_video_resource_for_system(self, *, video_id: str) -> VideoResourceRecord | None:
        """System-only query hook for background workers."""
        return self._repository.get_by_id_system(video_id)

    def mark_transcription_in_progress(self, *, video_id: str) -> None:
        """System-only hook: set transcribe status to TRANSCRIBING."""
        self._repository.update_transcription_status(video_id, TranscribeStatus.TRANSCRIBING)

    def mark_transcription_completed(self, *, video_id: str, full_transcript: str) -> None:
        """System-only hook: set transcribe status to COMPLETED with transcript payload."""
        self._repository.update_transcription_status(
            video_id,
            TranscribeStatus.COMPLETED,
            full_transcript=full_transcript,
        )

    def mark_transcription_failed(self, *, video_id: str) -> None:
        """System-only hook: set transcribe status to FAILED."""
        self._repository.update_transcription_status(video_id, TranscribeStatus.FAILED)

    def mark_frame_extraction_in_progress(self, *, video_id: str) -> None:
        """System-only hook: set frame extraction status to EXTRACTING."""
        self._repository.update_frame_extraction(video_id, FrameExtractionStatus.EXTRACTING)

    def mark_frame_extraction_completed(
        self,
        *,
        video_id: str,
        keyframes: list[dict],
        keyframes_oss_prefix: str,
    ) -> None:
        """System-only hook: set frame extraction status to COMPLETED with extracted keyframes."""
        self._repository.update_frame_extraction(
            video_id,
            FrameExtractionStatus.COMPLETED,
            keyframes=keyframes,
            keyframes_oss_prefix=keyframes_oss_prefix,
        )

    def mark_frame_extraction_failed(self, *, video_id: str) -> None:
        """System-only hook: set frame extraction status to FAILED."""
        self._repository.update_frame_extraction(video_id, FrameExtractionStatus.FAILED)

    def mark_extract_completed_if_ready(self, *, video_id: str) -> bool:
        """System-only hook: set extract_completed_at when dual extraction status is ready."""
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return False

        self._repository.update_extract_completed_at(video_id)
        return True

    def mark_deletion_in_progress(self, *, video_id: str) -> None:
        """System-only hook: advance deletion state machine to DELETING."""
        self._repository.update_deletion_status(video_id, "DELETING")

    def mark_deletion_failed(self, *, video_id: str) -> None:
        """System-only hook: record cleanup failure as DELETE_FAILED."""
        self._repository.update_deletion_status(video_id, "DELETE_FAILED")

    def purge_video(self, *, video_id: str) -> None:
        """System-only hook: physical delete after all external resources are cleaned."""
        self._repository.physical_delete(video_id)

    def _to_view(self, record: VideoResourceRecord) -> VideoResourceView:
        payload = asdict(record)
        keyframes = payload.get("keyframes")
        payload["keyframes"] = None if keyframes is None else [KeyFrameItem.model_validate(item) for item in keyframes]
        return VideoResourceView.model_validate(payload)
