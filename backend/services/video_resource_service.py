from __future__ import annotations

from dataclasses import asdict
import logging

from backend.api.pagination import build_pagination, normalize_page_size
from backend.infrastructure.storage.oss_client import get_object_storage_client
from backend.models.enums import FrameExtractionStatus, TranscribeStatus
from backend.repositories.video_resource_repository import VideoResourceRecord, VideoResourceRepository
from backend.services.progress_publish_service import ProgressPublishService
from backend.schemas.video_resource import KeyFrameItem, VideoResourceCreateRequest, VideoResourceUpdateRequest, VideoResourceView
from backend.websocket.schemas import WSScope, WSStage


logger = logging.getLogger(__name__)


def _dispatch_async_cascade_delete(video_id: str) -> None:
    from backend.tasks.video_cleanup_tasks import async_cascade_delete_video

    async_cascade_delete_video.delay(video_id)


def _dispatch_async_process_video(video_id: str, trace_id: str = "") -> None:
    from backend.tasks.video_summary_tasks import async_process_video

    async_process_video.delay(video_id, trace_id)


class VideoResourceService:
    def __init__(
        self,
        repository: VideoResourceRepository,
        progress_publisher: ProgressPublishService | None = None,
    ) -> None:
        self._repository = repository
        self._progress_publisher = progress_publisher

    def _publish_status_update(
        self,
        *,
        user_id: str,
        video_id: str,
        status: str,
        previous_status: str | None,
        message: str,
    ) -> None:
        if self._progress_publisher is None:
            return
        try:
            self._progress_publisher.publish_status_update(
                user_id=user_id,
                scope=WSScope.VIDEO_RESOURCE,
                scope_id=video_id,
                status=status,
                previous_status=previous_status,
                message=message,
                extra={"video_id": video_id},
            )
        except Exception:
            logger.exception("Failed to publish status_update for video_id=%s", video_id)

    def _publish_progress(
        self,
        *,
        user_id: str,
        video_id: str,
        stage: WSStage,
        status: str,
        progress: int,
        message: str,
    ) -> None:
        if self._progress_publisher is None:
            return
        try:
            self._progress_publisher.publish_progress(
                user_id=user_id,
                scope=WSScope.VIDEO_RESOURCE,
                scope_id=video_id,
                stage=stage,
                status=status,
                progress=progress,
                message=message,
            )
        except Exception:
            logger.exception("Failed to publish progress for video_id=%s", video_id)

    def _publish_error(
        self,
        *,
        user_id: str,
        video_id: str,
        code: str,
        message: str,
        is_retryable: bool,
    ) -> None:
        if self._progress_publisher is None:
            return
        try:
            self._progress_publisher.publish_error(
                user_id=user_id,
                scope=WSScope.VIDEO_RESOURCE,
                scope_id=video_id,
                code=code,
                message=message,
                is_retryable=is_retryable,
            )
        except Exception:
            logger.exception("Failed to publish error event for video_id=%s", video_id)

    def _publish_completed(self, *, user_id: str, video_id: str, message: str) -> None:
        if self._progress_publisher is None:
            return
        try:
            self._progress_publisher.publish_completed(
                user_id=user_id,
                scope=WSScope.VIDEO_RESOURCE,
                scope_id=video_id,
                message=message,
                result={"video_id": video_id},
            )
        except Exception:
            logger.exception("Failed to publish completed event for video_id=%s", video_id)

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

    def trigger_processing_after_upload(self, *, video_id: str, trace_id: str = "") -> bool:
        """System-only hook: trigger async extraction pipeline after upload is finalized."""
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return False
        if video.is_deleted:
            return False
        if not (video.oss_key and video.oss_key.strip()):
            return False

        try:
            _dispatch_async_process_video(video_id, trace_id)
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
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return
        self._repository.update_transcription_status(video_id, TranscribeStatus.TRANSCRIBING)
        self._publish_status_update(
            user_id=video.owner_id,
            video_id=video_id,
            status="TRANSCRIBING",
            previous_status=video.transcribe_status,
            message="Transcription started",
        )
        self._publish_progress(
            user_id=video.owner_id,
            video_id=video_id,
            stage=WSStage.TRANSCRIBING,
            status="RUNNING",
            progress=20,
            message="Transcribing audio",
        )

    def mark_transcription_completed(self, *, video_id: str, full_transcript: str) -> None:
        """System-only hook: set transcribe status to COMPLETED with transcript payload."""
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return
        self._repository.update_transcription_status(
            video_id,
            TranscribeStatus.COMPLETED,
            full_transcript=full_transcript,
        )
        self._publish_status_update(
            user_id=video.owner_id,
            video_id=video_id,
            status="TRANSCRIBE_COMPLETED",
            previous_status=video.transcribe_status,
            message="Transcription completed",
        )
        self._publish_progress(
            user_id=video.owner_id,
            video_id=video_id,
            stage=WSStage.TRANSCRIBING,
            status="COMPLETED",
            progress=50,
            message="Transcript is ready",
        )

    def mark_transcription_failed(self, *, video_id: str) -> None:
        """System-only hook: set transcribe status to FAILED."""
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return
        self._repository.update_transcription_status(video_id, TranscribeStatus.FAILED)
        self._publish_status_update(
            user_id=video.owner_id,
            video_id=video_id,
            status="FAILED",
            previous_status=video.transcribe_status,
            message="Transcription failed",
        )
        self._publish_error(
            user_id=video.owner_id,
            video_id=video_id,
            code="TRANSCRIBE_FAILED",
            message="Transcription failed",
            is_retryable=True,
        )

    def mark_frame_extraction_in_progress(self, *, video_id: str) -> None:
        """System-only hook: set frame extraction status to EXTRACTING."""
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return
        self._repository.update_frame_extraction(video_id, FrameExtractionStatus.EXTRACTING)
        self._publish_status_update(
            user_id=video.owner_id,
            video_id=video_id,
            status="EXTRACTING_KEYFRAMES",
            previous_status=video.frame_extraction_status,
            message="Keyframe extraction started",
        )
        self._publish_progress(
            user_id=video.owner_id,
            video_id=video_id,
            stage=WSStage.EXTRACTING_KEYFRAMES,
            status="RUNNING",
            progress=25,
            message="Extracting keyframes",
        )

    def mark_frame_extraction_completed(
        self,
        *,
        video_id: str,
        keyframes: list[dict],
        keyframes_oss_prefix: str,
    ) -> None:
        """System-only hook: set frame extraction status to COMPLETED with extracted keyframes."""
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return
        self._repository.update_frame_extraction(
            video_id,
            FrameExtractionStatus.COMPLETED,
            keyframes=keyframes,
            keyframes_oss_prefix=keyframes_oss_prefix,
        )
        self._publish_status_update(
            user_id=video.owner_id,
            video_id=video_id,
            status="KEYFRAMES_COMPLETED",
            previous_status=video.frame_extraction_status,
            message="Keyframe extraction completed",
        )
        self._publish_progress(
            user_id=video.owner_id,
            video_id=video_id,
            stage=WSStage.EXTRACTING_KEYFRAMES,
            status="COMPLETED",
            progress=75,
            message="Keyframes are ready",
        )

    def mark_frame_extraction_failed(self, *, video_id: str) -> None:
        """System-only hook: set frame extraction status to FAILED."""
        video = self._repository.get_by_id_system(video_id)
        if video is None:
            return
        self._repository.update_frame_extraction(video_id, FrameExtractionStatus.FAILED)
        self._publish_status_update(
            user_id=video.owner_id,
            video_id=video_id,
            status="FAILED",
            previous_status=video.frame_extraction_status,
            message="Keyframe extraction failed",
        )
        self._publish_error(
            user_id=video.owner_id,
            video_id=video_id,
            code="EXTRACT_KEYFRAMES_FAILED",
            message="Keyframe extraction failed",
            is_retryable=True,
        )

    def mark_extract_completed_if_ready(self, *, video_id: str) -> bool:
        """System-only hook: set extract_completed_at when dual extraction status is ready."""
        before = self._repository.get_by_id_system(video_id)
        if before is None:
            return False

        self._repository.update_extract_completed_at(video_id)
        after = self._repository.get_by_id_system(video_id)
        if after is None or after.extract_completed_at is None:
            return False

        self._publish_status_update(
            user_id=after.owner_id,
            video_id=video_id,
            status="READY",
            previous_status=None,
            message="Video extraction pipeline completed",
        )
        self._publish_completed(
            user_id=after.owner_id,
            video_id=video_id,
            message="Video is ready for summary task creation",
        )
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
        if record.oss_key:
            payload["presigned_url"] = get_object_storage_client().get_presigned_url(object_key=record.oss_key)
        else:
            payload["presigned_url"] = None
        return VideoResourceView.model_validate(payload)
