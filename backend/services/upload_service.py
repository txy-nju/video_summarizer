"""
上传服务：分片管理与合并的业务编排。

职责：
- 初始化上传会话
- 校验分片完整性
- 写入分片数据
- 查询上传进度
- 取消上传
- 触发最终合并任务（Celery）
"""

from __future__ import annotations

import logging
import uuid

from backend.exceptions import ConflictError, ErrorCode, ForbiddenError, NotFoundError, ServiceError, ValidationError
from backend.repositories.upload_repository import UploadRepository
from backend.schemas.upload import (
    ChunkStatusResponse,
    InitUploadRequest,
    InitUploadResponse,
    SERVER_CHUNK_SIZE,
    UploadCancelResponse,
    UploadSessionState,
)

logger = logging.getLogger(__name__)


class UploadService:
    def __init__(self, repository: UploadRepository) -> None:
        self._repository = repository

    def initiate_upload(
        self,
        *,
        owner_id: str,
        payload: InitUploadRequest,
    ) -> InitUploadResponse:
        """创建新的上传会话。"""
        upload_id = str(uuid.uuid4())
        state = self._repository.create_session(
            upload_id=upload_id,
            owner_id=owner_id,
            file_name=payload.file_name,
            total_size=payload.total_size,
            chunk_size=SERVER_CHUNK_SIZE,
            video_id=payload.video_id,
        )
        return InitUploadResponse(
            upload_id=state.upload_id,
            chunk_size=state.chunk_size,
            expires_at=state.expires_at,
        )

    def upload_chunk(
        self,
        *,
        upload_id: str,
        owner_id: str,
        chunk_index: int,
        data: bytes,
        trace_id: str = "",
    ) -> UploadSessionState:
        """上传单个分片。

        前置校验：
        - 会话存在且属于当前用户
        - 分片索引在合法范围内
        - 分片大小不超过 chunk_size（最后一个分片可不满）
        - 分片尚未上传（幂等：已存在则跳过写入）

        Raises:
            AppError: 校验失败
        """
        state = self._repository.get_session(upload_id)
        if state is None:
            raise NotFoundError(
                code=ErrorCode.UPLOAD_SESSION_NOT_FOUND,
                message=f"Upload session not found: {upload_id}",
            )
        if state.owner_id != owner_id:
            raise ForbiddenError(
                code=ErrorCode.UPLOAD_SESSION_NOT_OWNER,
                message="Upload session does not belong to current user",
            )
        if state.state in ("done", "dedup_reused", "rejected", "failed", "cancelled"):
            raise ConflictError(
                code=ErrorCode.UPLOAD_SESSION_TERMINAL_STATE,
                message=f"Upload session is in terminal state: {state.state}",
            )

        total_chunks = state.total_chunks
        if chunk_index < 0 or chunk_index >= total_chunks:
            raise ValidationError(
                code=ErrorCode.UPLOAD_CHUNK_INDEX_OUT_OF_RANGE,
                message=f"Invalid chunk index {chunk_index}: must be 0-{total_chunks - 1}",
            )

        expected_length = state.chunk_length(chunk_index)
        if len(data) != expected_length:
            raise ValidationError(
                code=ErrorCode.UPLOAD_CHUNK_SIZE_MISMATCH,
                message=f"Chunk {chunk_index} has wrong size: expected {expected_length}, got {len(data)}",
            )

        # 幂等：已上传的分片跳过磁盘写入，仅确认
        if chunk_index not in state.uploaded_chunks:
            self._repository.write_chunk(upload_id, chunk_index, data)

        state = self._repository.mark_chunk_uploaded(upload_id, chunk_index)
        if state is None:
            raise ServiceError(
                code=ErrorCode.UPLOAD_FINALIZE_FAILED,
                message=f"Failed to update session after chunk upload: {upload_id}",
            )

        # 所有分片完成后标记为 uploading_complete，触发最终合并
        if state.is_complete:
            self._repository.update_state(upload_id, "uploading_complete")
            self._dispatch_finalize(upload_id, owner_id, state.file_name, trace_id=trace_id)

        return state

    def get_upload_status(self, *, upload_id: str, owner_id: str) -> ChunkStatusResponse | None:
        """查询上传进度。"""
        state = self._repository.get_session(upload_id)
        if state is None:
            return None
        if state.owner_id != owner_id:
            return None
        return ChunkStatusResponse(
            upload_id=state.upload_id,
            uploaded_size=state.uploaded_size,
            total_size=state.total_size,
            uploaded_chunks=sorted(state.uploaded_chunks),
            video_id=state.video_id,
            state=state.state,
        )

    def cancel_upload(self, *, upload_id: str, owner_id: str) -> UploadCancelResponse | None:
        """取消上传会话，清理分片文件。"""
        state = self._repository.get_session(upload_id)
        if state is None:
            return None
        if state.owner_id != owner_id:
            return None
        self._repository.update_state(upload_id, "cancelled")
        self._repository.cleanup_chunks(upload_id)
        self._repository.delete_session(upload_id)
        return UploadCancelResponse(upload_id=upload_id)

    def finalize_upload(self, *, upload_id: str) -> dict:
        """最终合并分片（由 Celery 任务调用）。

        此方法不校验 owner_id（由 Celery 任务直接调用，非用户请求上下文）。

        Returns:
            {"upload_id": ..., "status": "MERGED", "merged_path": ..., "owner_id": ..., "file_name": ...}
        """
        state = self._repository.get_session(upload_id)
        if state is None:
            return {"upload_id": upload_id, "status": "NOT_FOUND"}

        if state.state in ("done", "dedup_reused", "rejected", "failed", "cancelled"):
            return {"upload_id": upload_id, "status": state.state.upper()}

        try:
            self._repository.update_state(upload_id, "finalizing")

            # 合并分片
            merged_path = self._repository.merge_chunks(upload_id, state.total_chunks)

            # 返回合并结果给调用方（Celery 任务负责后续 DB 写入与事件发布）
            logger.info("Chunks merged for upload_id=%s, path=%s", upload_id, merged_path)
            return {
                "upload_id": upload_id,
                "status": "MERGED",
                "merged_path": str(merged_path.resolve()),
                "owner_id": state.owner_id,
                "file_name": state.file_name,
                "total_size": state.total_size,
                "video_id": state.video_id,
            }

        except Exception as exc:
            logger.exception("Upload finalization failed: upload_id=%s", upload_id)
            self._repository.update_state(upload_id, "failed")
            return {"upload_id": upload_id, "status": "FAILED", "error": str(exc)}

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _dispatch_finalize(self, upload_id: str, owner_id: str, file_name: str, trace_id: str = "") -> None:
        """派发 Celery 任务执行分片合并与 oss_key 写入。"""
        try:
            from backend.tasks.upload_finalize_tasks import async_finalize_upload

            async_finalize_upload.delay(upload_id, trace_id)
            logger.info("Dispatched upload finalization: upload_id=%s", upload_id)
        except Exception as exc:
            logger.warning("Failed to dispatch upload finalization: upload_id=%s, error=%s", upload_id, exc)

