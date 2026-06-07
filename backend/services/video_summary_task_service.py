from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
import logging

from backend.api.pagination import build_pagination, normalize_page_size
from backend.repositories.kb_repository import KnowledgeBaseRepository
from backend.repositories.video_resource_repository import VideoResourceRepository
from backend.repositories.video_summary_task_repository import VideoSummaryTaskRecord, VideoSummaryTaskRepository
from backend.schemas.video_summary_task import (
    TaskCloneToKbRequest,
    VideoSummaryTaskCreateRequest,
    VideoSummaryTaskUpdateRequest,
    VideoSummaryTaskView,
)


logger = logging.getLogger(__name__)

_WORKFLOW_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT_GENERATING": {"WAITING_USER_APPROVAL", "FAILED"},
    "WAITING_USER_APPROVAL": {"FINAL_GENERATING", "FAILED"},
    "FINAL_GENERATING": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
}


class DuplicateTaskError(Exception):
    """Raised when a KB already has a Task for the same video."""

    def __init__(self, existing_task_id: str, kbid: str) -> None:
        self.existing_task_id = existing_task_id
        self.kbid = kbid
        super().__init__(
            f"Task {existing_task_id} already exists in KB {kbid} for this video"
        )


class VideoSummaryTaskService:
    def __init__(
        self,
        repository: VideoSummaryTaskRepository,
        kb_repository: KnowledgeBaseRepository,
        video_repository: VideoResourceRepository,
    ) -> None:
        self._repository = repository
        self._kb_repository = kb_repository
        self._video_repository = video_repository

    def create_video_summary_task(self, *, owner_id: str, payload: VideoSummaryTaskCreateRequest) -> VideoSummaryTaskView | None:
        kb = self._kb_repository.get_by_owner_and_id(owner_id, payload.kbid)
        video = self._video_repository.get_by_owner_and_id(owner_id, payload.video_id)
        if kb is None or video is None:
            return None

        # 验证视频资源已就绪（转录与关键帧抽取均完成）
        if (
            video.extract_completed_at is None
            or video.transcribe_status != "COMPLETED"
            or video.frame_extraction_status != "COMPLETED"
        ):
            raise ValueError("video_not_ready")

        # Check for duplicate: a KB should only have one Task per video
        existing = self._repository.find_by_kb_and_video(owner_id, payload.kbid, payload.video_id)
        if existing is not None:
            if payload.replace_existing_task_id == existing.task_id:
                # Replace: delete the old Task, then create the new one.
                # delete_by_owner_and_id atomically decrements ref_count
                # inside the same transaction; create() atomically
                # increments it — net-zero change.
                logger.info(
                    "Replacing existing task_id=%s in kbid=%s video_id=%s with new task",
                    existing.task_id, payload.kbid, payload.video_id,
                )
                self._repository.delete_by_owner_and_id(owner_id, existing.task_id)
            else:
                raise DuplicateTaskError(existing_task_id=existing.task_id, kbid=payload.kbid)

        record = self._repository.create(
            owner_id=owner_id,
            kbid=payload.kbid,
            video_id=payload.video_id,
            user_initial_preference=payload.user_initial_preference,
        )

        # 建立 KB↔Video 关联（幂等；与 POST /kb/{kbid}/videos 的显式绑定不冲突）
        self._kb_repository.add_video_to_kb(owner_id, payload.kbid, payload.video_id)

        # 异步索引视频 transcript 向量到目标 KB collection（幂等；重复调用安全）
        from backend.tasks.global_retrieval_tasks import async_add_video_to_vector_collection

        async_add_video_to_vector_collection.delay(payload.kbid, payload.video_id)

        return self._to_view(record)

    def list_video_summary_tasks(self, *, owner_id: str, page: int, page_size: int) -> tuple[list[VideoSummaryTaskView], dict]:
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

    def get_video_summary_task(self, *, owner_id: str, task_id: str) -> VideoSummaryTaskView | None:
        record = self._repository.get_by_owner_and_id(owner_id, task_id)
        if record is None:
            return None
        return self._to_view(record)

    def update_video_summary_task(
        self,
        *,
        owner_id: str,
        task_id: str,
        payload: VideoSummaryTaskUpdateRequest,
    ) -> VideoSummaryTaskView | None:
        record = self._repository.update_by_owner_and_id(
            owner_id=owner_id,
            task_id=task_id,
            draft_summary=payload.draft_summary,
            user_guidance=payload.user_guidance,
            title=payload.title,
        )
        if record is None:
            return None
        return self._to_view(record)

    def list_tasks_by_video_id(self, *, owner_id: str, video_id: str) -> list[VideoSummaryTaskView]:
        """List all summary tasks that reference a specific video."""
        records = self._repository.list_by_video_id(owner_id, video_id)
        return [self._to_view(record) for record in records]

    def clone_task_to_kb(self, *, owner_id: str, task_id: str, payload: TaskCloneToKbRequest) -> VideoSummaryTaskView:
        """Clone an existing Task to another Knowledge Base.

        The clone gets a new task_id and the target kbid; all analysis fields
        are copied verbatim.  Additionally, the video is linked to the target KB
        and its transcript vectors are indexed for KB-level RAG retrieval —
        making the cloned Task indistinguishable from one created directly in
        that KB.
        """
        # 1. Validate source Task ownership
        source = self._repository.get_by_owner_and_id(owner_id, task_id)
        if source is None:
            raise LookupError("Task not found")

        # 2. Validate target KB ownership
        target_kb = self._kb_repository.get_by_owner_and_id(owner_id, payload.kbid)
        if target_kb is None:
            raise LookupError("Target KB not found")

        # 3. Duplicate check (ref_count is handled atomically inside
        #    delete_by_owner_and_id + clone_to_kb)
        existing = self._repository.find_by_kb_and_video(owner_id, payload.kbid, source.video_id)
        if existing is not None:
            if payload.replace_existing_task_id == existing.task_id:
                logger.info(
                    "Replacing existing task_id=%s in kbid=%s (clone target) with clone of task_id=%s",
                    existing.task_id, payload.kbid, task_id,
                )
                self._repository.delete_by_owner_and_id(owner_id, existing.task_id)
            else:
                raise DuplicateTaskError(existing_task_id=existing.task_id, kbid=payload.kbid)

        # 4. Clone the Task row (ref_count +1 is atomic with the INSERT)
        clone = self._repository.clone_to_kb(
            source_task_id=task_id,
            target_kbid=payload.kbid,
            owner_id=owner_id,
        )

        # 5. Ensure KB↔Video relation exists (idempotent)
        self._kb_repository.add_video_to_kb(owner_id, payload.kbid, source.video_id)

        # 6. Index video transcript vectors into the target KB (idempotent, async)
        from backend.tasks.global_retrieval_tasks import async_add_video_to_vector_collection

        async_add_video_to_vector_collection.delay(payload.kbid, source.video_id)

        return self._to_view(clone)

    def delete_video_summary_task(self, *, owner_id: str, task_id: str) -> bool:
        # Collect task info before deletion (for ref counting + GC)
        task = self._repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            return False

        video_id = task.video_id
        # Collect linked KB ids before deletion for GC vector cleanup
        linked_kbids: list[str] = []
        try:
            linked_kbids = self._video_repository.get_linked_kb_ids_for_video(video_id)
        except Exception:
            logger.exception(
                "Failed to collect linked_kbids for video_id=%s before task deletion",
                video_id,
            )

        deleted = self._repository.delete_by_owner_and_id(owner_id, task_id)
        if not deleted:
            return False

        # ref_count was atomically decremented inside delete_by_owner_and_id.
        # Re-read to decide about GC (separate session sees the committed value).
        try:
            current_ref_count = self._video_repository.get_ref_count(video_id)
            if current_ref_count == 0:
                self._trigger_garbage_collection(video_id=video_id, linked_kbids=linked_kbids)
        except Exception:
            logger.exception(
                "Failed to read ref_count / dispatch GC for video_id=%s",
                video_id,
            )

        return True

    def transition_workflow_state(
        self,
        *,
        owner_id: str,
        task_id: str,
        next_state: str,
        draft_summary: str | None = None,
        user_guidance: str | None = None,
        title: str | None = None,
        final_summary: str | None = None,
    ) -> VideoSummaryTaskView | None:
        """System-only transition hook for workflow lifecycle orchestration."""
        record = self._repository.get_by_owner_and_id(owner_id, task_id)
        if record is None:
            return None

        current_state = record.workflow_state
        if current_state == next_state:
            return self._to_view(record)

        allowed = _WORKFLOW_TRANSITIONS.get(current_state, set())
        if next_state not in allowed:
            raise ValueError(f"invalid_workflow_transition:{current_state}->{next_state}")

        if any(value is not None for value in (draft_summary, user_guidance, title, final_summary)):
            updated = self._repository.update_by_owner_and_id(
                owner_id=owner_id,
                task_id=task_id,
                draft_summary=draft_summary,
                user_guidance=user_guidance,
                title=title,
                final_summary=final_summary,
            )
            if updated is None:
                return None

        transitioned = self._repository.update_state_by_owner_and_id(
            owner_id=owner_id,
            task_id=task_id,
            workflow_state=next_state,
        )
        if transitioned is None:
            return None
        return self._to_view(transitioned)

    def mark_analysis_completed(
        self,
        *,
        owner_id: str,
        task_id: str,
        aggregated_chunk_insights: str,
        title: str | None = None,
    ) -> VideoSummaryTaskView | None:
        return self.transition_workflow_state(
            owner_id=owner_id,
            task_id=task_id,
            next_state="WAITING_USER_APPROVAL",
            draft_summary=aggregated_chunk_insights,
            title=title,
        )

    def mark_finalization_started(
        self,
        *,
        owner_id: str,
        task_id: str,
        user_guidance: str | None = None,
    ) -> VideoSummaryTaskView | None:
        return self.transition_workflow_state(
            owner_id=owner_id,
            task_id=task_id,
            next_state="FINAL_GENERATING",
            user_guidance=user_guidance,
        )

    def mark_finalization_completed(
        self,
        *,
        owner_id: str,
        task_id: str,
        final_summary: str,
    ) -> VideoSummaryTaskView | None:
        return self.transition_workflow_state(
            owner_id=owner_id,
            task_id=task_id,
            next_state="COMPLETED",
            final_summary=final_summary,
        )

    def mark_workflow_failed(self, *, owner_id: str, task_id: str) -> VideoSummaryTaskView | None:
        record = self._repository.get_by_owner_and_id(owner_id, task_id)
        if record is None:
            return None
        if record.workflow_state in ("COMPLETED", "FAILED"):
            raise ValueError(f"invalid_workflow_transition:{record.workflow_state}->FAILED")
        return self.transition_workflow_state(
            owner_id=owner_id,
            task_id=task_id,
            next_state="FAILED",
        )

    def dispatch_start_analysis_workflow(
        self,
        *,
        owner_id: str,
        task_id: str,
        trace_id: str,
    ) -> dict[str, str]:
        """Validate task/video context and dispatch phase-1 analysis Celery task.

        Idempotency guards:
        - WAITING_USER_APPROVAL: phase-1 already completed, return cached draft_summary.
        - COMPLETED: task fully completed, return cached final_summary.
        - FINAL_GENERATING: phase-2 in progress, reject with ValueError.
        """
        task = self._repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            raise LookupError("Video summary task not found")

        # ── 幂等性守卫 ──────────────────────────────────────────────
        # 所有数据来自 DB（task 由 get_by_owner_and_id 查询），不存在缓存失效问题。
        # draft_summary / final_summary 与 workflow_state 在同一事务中写入，保证一致性。

        # phase-1 已完成：直接返回 DB 中已持久化的 draft_summary
        if task.workflow_state == "WAITING_USER_APPROVAL":
            if not task.draft_summary:
                logger.warning(
                    "Data integrity: workflow_state=WAITING_USER_APPROVAL but draft_summary is empty for task_id=%s",
                    task_id,
                )
            return {
                "task_id": task_id,
                "workflow_state": "WAITING_USER_APPROVAL",
                "draft_summary": task.draft_summary or "",
                "message": "Phase-1 analysis already completed. Returning persisted draft_summary.",
            }

        # 全部已完成：直接返回 DB 中已持久化的 final_summary
        if task.workflow_state == "COMPLETED":
            if not task.final_summary:
                logger.warning(
                    "Data integrity: workflow_state=COMPLETED but final_summary is empty for task_id=%s",
                    task_id,
                )
            return {
                "task_id": task_id,
                "workflow_state": "COMPLETED",
                "final_summary": task.final_summary or "",
                "message": "Task already completed. Returning persisted final_summary.",
            }

        # phase-2 正在执行中：拒绝回到 phase-1
        if task.workflow_state == "FINAL_GENERATING":
            raise ValueError("finalization_in_progress")
        # ────────────────────────────────────────────────────────────

        video = self._video_repository.get_by_owner_and_id(owner_id=owner_id, video_id=task.video_id)
        if video is None:
            raise LookupError("Video resource not found")

        # chunk_planner_node / chunk_audio_analyzer 期望收到形如
        # {"text": "...", "segments": [...]} 的 JSON 字符串。
        # full_transcript 只存储了平文本，需结合 transcript_segments 重建。
        transcript_text = video.full_transcript or ""
        transcript_segments = video.transcript_segments or []
        if transcript_segments:
            transcript = json.dumps(
                {"text": transcript_text, "segments": transcript_segments},
                ensure_ascii=False,
            )
        else:
            transcript = transcript_text
        keyframes = video.keyframes or []

        from backend.tasks.workflow_runtime_tasks import async_execute_analysis_workflow

        # Update state synchronously to prevent race conditions in clients
        self._repository.update_state_by_owner_and_id(
            owner_id=owner_id,
            task_id=task_id,
            workflow_state="DRAFT_GENERATING"
        )

        task_result = async_execute_analysis_workflow.apply_async(
            args=[
                owner_id,
                task_id,
                transcript,
                keyframes,
                task.user_initial_preference or "",
                trace_id,
            ],
            queue="celery",
        )

        return {
            "task_id": task_id,
            "celery_task_id": task_result.id,
            "thread_id": task_id,
            "workflow_state": "DRAFT_GENERATING",
            "accepted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "message": "Phase-1 analysis workflow dispatched",
        }

    def dispatch_approve_and_finalize_workflow(
        self,
        *,
        owner_id: str,
        task_id: str,
        edited_aggregated_chunk_insights: str,
        human_guidance: str,
        trace_id: str,
    ) -> dict[str, str]:
        """Validate approval state and dispatch phase-2 finalization Celery task."""
        task = self._repository.get_by_owner_and_id(owner_id, task_id)
        if task is None:
            raise LookupError("Video summary task not found")

        if task.workflow_state != "WAITING_USER_APPROVAL":
            raise ValueError(
                f"Task must be in WAITING_USER_APPROVAL state, got {task.workflow_state}"
            )

        from backend.tasks.workflow_runtime_tasks import async_execute_finalization_workflow
        
        # Update state synchronously to prevent race conditions in clients
        self._repository.update_state_by_owner_and_id(
            owner_id=owner_id,
            task_id=task_id,
            workflow_state="FINAL_GENERATING"
        )

        task_result = async_execute_finalization_workflow.apply_async(
            args=[
                owner_id,
                task_id,
                edited_aggregated_chunk_insights,
                human_guidance,
                trace_id,
            ],
            queue="celery",
        )

        return {
            "task_id": task_id,
            "celery_task_id": task_result.id,
            "thread_id": task_id,
            "workflow_state": "FINAL_GENERATING",
            "accepted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "message": "Phase-2 finalization workflow dispatched",
        }

    def _trigger_garbage_collection(self, *, video_id: str, linked_kbids: list[str]) -> None:
        """Dispatch async GC when a video's task_ref_count drops to zero."""
        try:
            from backend.tasks.video_cleanup_tasks import async_garbage_collect_video

            async_garbage_collect_video.delay(video_id, linked_kbids)
            logger.info(
                "Dispatched GC for video_id=%s (ref_count=0, kbids=%s)",
                video_id, linked_kbids,
            )
        except Exception:
            logger.exception(
                "Failed to dispatch GC for video_id=%s", video_id,
            )

    def _to_view(self, record: VideoSummaryTaskRecord) -> VideoSummaryTaskView:
        payload = asdict(record)
        payload.pop("owner_id", None)
        # For single-task lookups where kb_name wasn't populated by the
        # repository query (create / clone / get), fill it in from the
        # KB record that the service already fetched during validation.
        if not payload.get("kb_name"):
            try:
                kb = self._kb_repository.get_by_owner_and_id(
                    record.owner_id, record.kbid,
                )
                if kb is not None:
                    payload["kb_name"] = kb.name
            except Exception:
                pass
        return VideoSummaryTaskView.model_validate(payload)
