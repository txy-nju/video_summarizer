"""WorkflowOrchestrationService - Coordinate core LangGraph workflow execution with backend lifecycle.

Bridges between:
- core/workflow/api.py (LangGraph engines: analyze_video, finalize_summary, answer_question_at_timestamp)
- backend services (progress publishing, task status tracking, state machine)
- FastAPI routes (workflow trigger, approval, time travel Q&A)

Key responsibilities:
- Start phase-1 analysis workflow asynchronously
- Manage workflow state machine (DRAFT_GENERATING → WAITING_USER_APPROVAL → FINAL_GENERATING → COMPLETED)
- Publish progress events to WebSocket + FCM
- Handle workflow callbacks (status updates)
- Recover from checkpoints (thread_id based)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

from core.workflow import (
    analyze_video,
    finalize_summary,
    answer_question_at_timestamp,
)
from backend.repositories.video_resource_repository import VideoResourceRepository
from backend.repositories.video_summary_task_repository import VideoSummaryTaskRepository
from backend.services.progress_publish_service import ProgressPublishService
from backend.services.task_status_service import TaskStatusService
from backend.services.workflow_notification_service import WorkflowNotificationService
from backend.websocket.schemas import WSEventType, WSScope, WSStage

logger = logging.getLogger(__name__)

# 进度心跳间隔（秒）：在长时间 LLM 调用期间定期向 WebSocket 发送 keepalive，
# 防止前端 180s 无消息超时断开。
_HEARTBEAT_INTERVAL_SECONDS = 30.0


class WorkflowOrchestrationService:
    """High-level workflow orchestration facade for backend integration.

    Encapsulates:
    - Phase-1 analysis trigger and state transitions
    - Phase-2 finalization trigger and approval workflow
    - Time travel Q&A checkpoint recovery
    - Progress event publishing and FCM notification
    """

    def __init__(
        self,
        task_repository: VideoSummaryTaskRepository,
        video_repository: VideoResourceRepository,
        progress_publisher: ProgressPublishService,
        task_status_service: TaskStatusService,
        notification_service: WorkflowNotificationService | None = None,
    ):
        """Initialize orchestration service dependencies.

        Args:
            task_repository: Persistence for video_summary_task records
            video_repository: Persistence for video_resource records
            progress_publisher: ProgressPublishService for event broadcasting
            task_status_service: Task status tracking and observable events
        """
        self._task_repository = task_repository
        self._video_repository = video_repository
        self._progress_publisher = progress_publisher
        self._task_status_service = task_status_service
        self._notification_service = notification_service

    async def _heartbeat_loop(
        self,
        user_id: str,
        task_id: str,
        trace_id: str,
        interval: float = _HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        """周期性向 WebSocket 发送 keepalive 消息，防止前端 180s 无消息超时断开。"""
        while True:
            await asyncio.sleep(interval)
            try:
                self._progress_publisher.publish_progress(
                    user_id=user_id,
                    scope=WSScope.VIDEO_SUMMARY_TASK,
                    scope_id=task_id,
                    stage=WSStage.ANALYSIS,
                    status="RUNNING",
                    progress=0,
                    message="任务正在进行中...",
                    trace_id=trace_id,
                )
            except Exception:
                logger.debug("heartbeat publish failed (non-critical)", exc_info=True)

    def _build_workflow_callback(
        self,
        user_id: str,
        task_id: str,
        trace_id: str,
    ) -> Callable[[str], None]:
        """Build a callback function to intercept workflow progress messages.

        Args:
            user_id: User owning the task
            task_id: Task ID being executed
            trace_id: Request trace ID for correlation

        Returns:
            Callback function that parses status_callback messages from LangGraph
        """

        def callback(message: str) -> None:
            """Process workflow progress messages.

            Messages are either:
            1. Plain text status (e.g., "⚙️ [LangGraph 初始化] ...")
            2. JSON chunk progress (e.g., "[[PROGRESS]]{...json...}")
            """
            if not message or not isinstance(message, str):
                return

            # Parse JSON chunk progress if present
            if message.startswith("[[PROGRESS]]"):
                try:
                    json_str = message[len("[[PROGRESS]]") :]
                    progress_data = json.loads(json_str)

                    stage_str = progress_data.get("stage", "running")
                    overall_percent = progress_data.get("overall_percent", 0)
                    stage_map = {
                        "running": WSStage.ANALYSIS,
                        "finished": WSStage.ANALYSIS,
                    }
                    stage = stage_map.get(stage_str, WSStage.ANALYSIS)

                    self._progress_publisher.publish_progress(
                        user_id=user_id,
                        scope=WSScope.VIDEO_SUMMARY_TASK,
                        scope_id=task_id,
                        stage=stage,
                        substage="chunk_processing",
                        status="RUNNING",
                        progress=overall_percent,
                        message=f"Chunk processing: {progress_data.get('overall_done', 0)}/{progress_data.get('overall_total', 0)}",
                        trace_id=trace_id,
                    )
                except json.JSONDecodeError:
                    logger.debug(f"Failed to parse progress JSON: {message}")
            else:
                # Emit status text as plain progress
                self._progress_publisher.publish_progress(
                    user_id=user_id,
                    scope=WSScope.VIDEO_SUMMARY_TASK,
                    scope_id=task_id,
                    stage=WSStage.ANALYSIS,
                    status="RUNNING",
                    message=message[:200],  # Truncate long messages
                    trace_id=trace_id,
                )

        return callback

    async def start_analysis_workflow_async(
        self,
        owner_id: str,
        task_id: str,
        transcript: str,
        keyframes: list[dict[str, Any]],
        user_initial_preference: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        """Trigger phase-1 analysis workflow asynchronously.

        Executes in executor to avoid blocking asyncio event loop.
        Updates task state to DRAFT_GENERATING.
        Publishes progress events during execution.

        Args:
            owner_id: User ID
            task_id: Task ID for state tracking
            transcript: Full video transcript
            keyframes: Extracted keyframes with metadata
            user_initial_preference: User's initial guidance for summary generation
            trace_id: Request trace ID for correlation

        Returns:
            Dict containing:
            - thread_id: Checkpoint recovery ID
            - stage: "pending_human_review"
            - workflow_state: Transitioned to WAITING_USER_APPROVAL
            - aggregated_chunk_insights: AI-generated analysis for review
            - editable_aggregated_chunk_insights: User-editable version
            - message: Status message
        """
        logger.info(
            f"[WorkflowOrch] Starting phase-1 analysis: task_id={task_id}, transcript_len={len(transcript)}, keyframes_count={len(keyframes)}"
        )

        # Update task state to DRAFT_GENERATING
        task_record = self._task_repository.update_state_by_owner_and_id(
            owner_id=owner_id,
            task_id=task_id,
            workflow_state="DRAFT_GENERATING",
        )
        if not task_record:
            raise ValueError(f"Task {task_id} not found or permission denied")

        # Emit status update event
        self._progress_publisher.publish_status_update(
            user_id=owner_id,
            scope=WSScope.VIDEO_SUMMARY_TASK,
            scope_id=task_id,
            status="DRAFT_GENERATING",
            message="Phase-1 analysis started",
            trace_id=trace_id,
        )

        # Run workflow in executor with keepalive heartbeat
        loop = asyncio.get_event_loop()
        callback = self._build_workflow_callback(user_id=owner_id, task_id=task_id, trace_id=trace_id)
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(owner_id, task_id, trace_id)
        )

        try:
            result = await loop.run_in_executor(
                None,
                lambda: analyze_video(
                    transcript=transcript,
                    keyframes=keyframes,
                    user_prompt=user_initial_preference
                    or "请结合画面与语音，给出一个全面、客观的高质量视频总结。",
                    status_callback=callback,
                    thread_id=task_id,  # Use task_id as checkpoint thread_id
                    trace_id=trace_id,
                ),
            )

            # Extract checkpoint thread_id from result
            thread_id = result.get("thread_id", task_id)

            # Update task with checkpoint thread_id and aggregated insights
            updated_task = self._task_repository.update_by_owner_and_id(
                owner_id=owner_id,
                task_id=task_id,
                draft_summary=result.get("aggregated_chunk_insights", ""),
                title=f"总结-{task_id[:8]}",
                workflow_state="WAITING_USER_APPROVAL",  # Transition to approval gate
            )

            if not updated_task:
                raise ValueError(f"Failed to update task {task_id}")

            # Emit completion event
            self._progress_publisher.publish_completed(
                user_id=owner_id,
                scope=WSScope.VIDEO_SUMMARY_TASK,
                scope_id=task_id,
                result={
                    "task_id": task_id,
                    "workflow_state": "WAITING_USER_APPROVAL",
                    "draft_summary": result.get("aggregated_chunk_insights", ""),
                },
                message="Phase-1 analysis completed. Awaiting human approval.",
                trace_id=trace_id,
            )

            if self._notification_service:
                self._notification_service.notify_workflow_approval_required(
                    user_id=owner_id,
                    task_id=task_id,
                    chunk_count=result.get("chunk_count", 0),
                    task_title=updated_task.title,
                )

            logger.info(
                f"[WorkflowOrch] Phase-1 completed: task_id={task_id}, thread_id={thread_id}, workflow_state=WAITING_USER_APPROVAL"
            )

            return {
                "thread_id": thread_id,
                "stage": "pending_human_review",
                "workflow_state": "WAITING_USER_APPROVAL",
                "aggregated_chunk_insights": result.get("aggregated_chunk_insights", ""),
                "editable_aggregated_chunk_insights": result.get("editable_aggregated_chunk_insights", ""),
                "message": "Phase-1 analysis completed. Ready for human approval.",
                "chunk_count": result.get("chunk_count", 0),
            }

        except Exception as e:
            logger.error(f"[WorkflowOrch] Phase-1 analysis failed: {e}", exc_info=True)
            # Transition to error state
            self._task_repository.update_state_by_owner_and_id(
                owner_id=owner_id,
                task_id=task_id,
                workflow_state="FAILED",
            )

            # Emit error event
            self._progress_publisher.publish_error(
                user_id=owner_id,
                scope=WSScope.VIDEO_SUMMARY_TASK,
                scope_id=task_id,
                message=f"Phase-1 analysis failed: {str(e)}",
                trace_id=trace_id,
            )

            if self._notification_service:
                self._notification_service.notify_workflow_failed(
                    user_id=owner_id,
                    task_id=task_id,
                    error_message=str(e),
                )

            raise
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def start_finalization_workflow_async(
        self,
        owner_id: str,
        task_id: str,
        edited_aggregated_chunk_insights: str = "",
        human_guidance: str = "",
        trace_id: str = "",
    ) -> str:
        """Trigger phase-2 finalization workflow after human approval.

        Executes in executor to avoid blocking asyncio event loop.
        Updates task state to FINAL_GENERATING.
        Publishes progress events during execution.

        Args:
            owner_id: User ID
            task_id: Task ID for state tracking
            edited_aggregated_chunk_insights: User-edited analysis insights
            human_guidance: User's guidance for final summary generation
            trace_id: Request trace ID for correlation

        Returns:
            Final summary text (draft_summary)
        """
        logger.info(
            f"[WorkflowOrch] Starting phase-2 finalization: task_id={task_id}, guidance_len={len(human_guidance)}"
        )

        # Retrieve task to get checkpoint thread_id
        task_record = self._task_repository.get_by_owner_and_id(owner_id, task_id)
        if not task_record:
            raise ValueError(f"Task {task_id} not found or permission denied")

        thread_id = task_id  # Use task_id as checkpoint thread_id

        # Update task state to FINAL_GENERATING
        self._task_repository.update_state_by_owner_and_id(
            owner_id=owner_id,
            task_id=task_id,
            workflow_state="FINAL_GENERATING",
        )

        # Emit status update event
        self._progress_publisher.publish_status_update(
            user_id=owner_id,
            scope=WSScope.VIDEO_SUMMARY_TASK,
            scope_id=task_id,
            status="FINAL_GENERATING",
            message="Phase-2 finalization started",
            trace_id=trace_id,
        )

        # Run workflow in executor with keepalive heartbeat
        loop = asyncio.get_event_loop()
        callback = self._build_workflow_callback(user_id=owner_id, task_id=task_id, trace_id=trace_id)
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(owner_id, task_id, trace_id)
        )

        try:
            final_summary = await loop.run_in_executor(
                None,
                lambda: finalize_summary(
                    thread_id=thread_id,
                    edited_aggregated_chunk_insights=edited_aggregated_chunk_insights,
                    human_guidance=human_guidance,
                    status_callback=callback,
                    trace_id=trace_id,
                ),
            )

            # Update task with final summary
            updated_task = self._task_repository.update_by_owner_and_id(
                owner_id=owner_id,
                task_id=task_id,
                final_summary=final_summary,
                workflow_state="COMPLETED",
            )

            if not updated_task:
                raise ValueError(f"Failed to update task {task_id}")

            # Emit completion event
            self._progress_publisher.publish_completed(
                user_id=owner_id,
                scope=WSScope.VIDEO_SUMMARY_TASK,
                scope_id=task_id,
                result={
                    "task_id": task_id,
                    "workflow_state": "COMPLETED",
                    "final_summary": final_summary,
                },
                message="Phase-2 finalization completed. Workflow finished.",
                trace_id=trace_id,
            )

            if self._notification_service:
                self._notification_service.notify_workflow_completed(
                    user_id=owner_id,
                    task_id=task_id,
                    task_title=updated_task.title,
                )

            logger.info(
                f"[WorkflowOrch] Phase-2 completed: task_id={task_id}, summary_len={len(final_summary)}, workflow_state=COMPLETED"
            )

            return final_summary

        except Exception as e:
            logger.error(f"[WorkflowOrch] Phase-2 finalization failed: {e}", exc_info=True)
            # Transition to error state
            self._task_repository.update_state_by_owner_and_id(
                owner_id=owner_id,
                task_id=task_id,
                workflow_state="FAILED",
            )

            # Emit error event
            self._progress_publisher.publish_error(
                user_id=owner_id,
                scope=WSScope.VIDEO_SUMMARY_TASK,
                scope_id=task_id,
                message=f"Phase-2 finalization failed: {str(e)}",
                trace_id=trace_id,
            )

            if self._notification_service:
                self._notification_service.notify_workflow_failed(
                    user_id=owner_id,
                    task_id=task_id,
                    error_message=str(e),
                )

            raise
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def start_time_travel_qa_async(
        self,
        owner_id: str,
        task_id: str,
        timestamp: str,
        question: str,
        window_seconds: int = 20,
        trace_id: str = "",
    ) -> str:
        """Execute time travel Q&A at specific timestamp within video.

        Checkpoint-based recovery: retrieves historical execution state
        and extracts evidence around target timestamp.

        Args:
            owner_id: User ID
            task_id: Task ID for state tracking
            timestamp: Target timestamp (HH:MM:SS format)
            question: Question to answer based on timestamp context
            window_seconds: Time window before/after timestamp for evidence extraction
            trace_id: Request trace ID for correlation

        Returns:
            Answer text with evidence-based context
        """
        logger.info(
            f"[WorkflowOrch] Starting time travel Q&A: task_id={task_id}, timestamp={timestamp}, window={window_seconds}s"
        )

        # Use task_id as checkpoint thread_id
        thread_id = task_id

        # Emit status event
        self._progress_publisher.publish_progress(
            user_id=owner_id,
            scope=WSScope.VIDEO_SUMMARY_TASK,
            scope_id=task_id,
            stage=WSStage.RAG_RETRIEVAL,
            status="RUNNING",
            message=f"Time travel Q&A at {timestamp}",
            trace_id=trace_id,
        )

        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        callback = self._build_workflow_callback(user_id=owner_id, task_id=task_id, trace_id=trace_id)

        try:
            answer = await loop.run_in_executor(
                None,
                lambda: answer_question_at_timestamp(
                    thread_id=thread_id,
                    timestamp=timestamp,
                    question=question,
                    window_seconds=window_seconds,
                    status_callback=callback,
                    trace_id=trace_id,
                ),
            )

            # Emit completion event
            self._progress_publisher.publish_completed(
                user_id=owner_id,
                scope=WSScope.VIDEO_SUMMARY_TASK,
                scope_id=task_id,
                message="Time travel Q&A completed",
                trace_id=trace_id,
            )

            logger.info(f"[WorkflowOrch] Time travel Q&A completed: task_id={task_id}, answer_len={len(answer)}")

            return answer

        except Exception as e:
            logger.error(f"[WorkflowOrch] Time travel Q&A failed: {e}", exc_info=True)

            # Emit error event
            self._progress_publisher.publish_error(
                user_id=owner_id,
                scope=WSScope.VIDEO_SUMMARY_TASK,
                scope_id=task_id,
                message=f"Time travel Q&A failed: {str(e)}",
                trace_id=trace_id,
            )

            raise
