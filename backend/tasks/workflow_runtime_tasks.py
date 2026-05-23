"""
WorkflowRuntimeTasks - Celery task entry points for asynchronous workflow execution.

Encapsulates:
- Phase-1 analysis workflow (async_execute_analysis_workflow)
- Phase-2 finalization workflow (async_execute_finalization_workflow)
- Time travel Q&A (async_execute_time_travel_qa)

Each task:
1. Reconstructs service dependencies from DB session
2. Calls WorkflowOrchestrationService methods (which are async)
3. Handles retry logic and state transitions
4. Records observable events for monitoring

Triggered by:
- API routes after user initiates workflow or provides approval
- NOT automatically triggered by previous steps (manual workflow control)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.db.session import SessionLocal
from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro: Any) -> Any:
    """Helper to run async functions in Celery worker context."""
    import asyncio
    import concurrent.futures

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # If there is a running event loop in this thread, run the coroutine in a new thread
        # so that we can block on its result without raising RuntimeError.
        def run_in_new_loop():
            return asyncio.run(coro)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_new_loop)
            return future.result()
    else:
        # Otherwise, run it in a new event loop on the current thread
        return asyncio.run(coro)


def _build_orchestration_service() -> Any:
    """Reconstruct WorkflowOrchestrationService from dependencies.

    Args:
        None (uses SessionLocal)

    Returns:
        WorkflowOrchestrationService instance
    """
    from backend.repositories.video_resource_repository import VideoResourceRepository
    from backend.repositories.video_summary_task_repository import VideoSummaryTaskRepository
    from backend.services.progress_event_bus import ProgressEventBus
    from backend.services.progress_publish_service import ProgressPublishService
    from backend.services.task_status_service import TaskStatusService
    from backend.services.workflow_notification_service import WorkflowNotificationService
    from backend.services.workflow_orchestration_service import WorkflowOrchestrationService
    from backend.config import get_settings
    from backend.notifications.fcm_service import FCMService
    from backend.repositories.device_repository import DeviceRepository

    db = SessionLocal()
    settings = get_settings()

    try:
        task_repo = VideoSummaryTaskRepository(db_session=db)
        video_repo = VideoResourceRepository(db_session=db)
        import redis as redis_lib
        redis_client = redis_lib.Redis.from_url(settings.celery_broker_url)
        event_bus = ProgressEventBus(redis_client=redis_client, instance_id="worker")
        progress_pub = ProgressPublishService(event_bus=event_bus, instance_id="worker")
        task_status_svc = TaskStatusService()
        notification_svc = WorkflowNotificationService(
            fcm_service=FCMService(),
            device_repository=DeviceRepository(db_session=db),
        )

        return WorkflowOrchestrationService(
            task_repository=task_repo,
            video_repository=video_repo,
            progress_publisher=progress_pub,
            task_status_service=task_status_svc,
            notification_service=notification_svc,
        )
    except Exception as e:
        logger.error(f"Failed to build orchestration service: {e}", exc_info=True)
        db.close()
        raise


@celery_app.task(
    name="backend.tasks.workflow_runtime_tasks.async_execute_analysis_workflow",
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
)
def async_execute_analysis_workflow(
    self,
    owner_id: str,
    task_id: str,
    transcript: str,
    keyframes: list[dict[str, Any]],
    user_initial_preference: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    """
    Phase-1 analysis workflow: execute in worker, avoid blocking request thread.

    Triggers:
    - From API: POST /api/v1/tasks/{task_id}/start-analysis
    - After video resources are ready (extract_completed_at is set)

    State transitions:
    - On entry: task.workflow_state = DRAFT_GENERATING (set by API before task dispatch)
    - On success: task.workflow_state = WAITING_USER_APPROVAL
    - On failure: task.workflow_state = FAILED

    Args:
        owner_id: User ID for authorization
        task_id: Task ID to update
        transcript: Full video transcript
        keyframes: Extracted keyframes with metadata
        user_initial_preference: User's initial guidance
        trace_id: Request correlation ID

    Returns:
        Dict with:
        - thread_id: Checkpoint recovery ID
        - workflow_state: WAITING_USER_APPROVAL on success
        - aggregated_chunk_insights: Analysis for human review
        - chunk_count: Number of chunks processed
        - message: Status message
    """
    logger.info(
        f"[WorkflowRuntimeTasks] Starting analysis workflow: task_id={task_id}, owner_id={owner_id}, trace_id={trace_id}"
    )

    db = SessionLocal()
    try:
        orchestration_service = _build_orchestration_service()

        # Run async workflow in event loop
        result = _run_async(
            orchestration_service.start_analysis_workflow_async(
                owner_id=owner_id,
                task_id=task_id,
                transcript=transcript,
                keyframes=keyframes,
                user_initial_preference=user_initial_preference,
                trace_id=trace_id,
            )
        )

        logger.info(
            f"[WorkflowRuntimeTasks] Analysis workflow completed: task_id={task_id}, workflow_state={result.get('workflow_state')}"
        )
        return result

    except ValueError as e:
        logger.warning(f"[WorkflowRuntimeTasks] Validation error: {e}")
        raise

    except Exception as e:
        logger.error(f"[WorkflowRuntimeTasks] Analysis workflow failed: {e}", exc_info=True)
        # Retry up to max_retries times
        raise self.retry(exc=e, countdown=60)  # type: ignore

    finally:
        db.close()


@celery_app.task(
    name="backend.tasks.workflow_runtime_tasks.async_execute_finalization_workflow",
    bind=True,
    acks_late=True,
    max_retries=3,
    default_retry_delay=60,
)
def async_execute_finalization_workflow(
    self,
    owner_id: str,
    task_id: str,
    edited_aggregated_chunk_insights: str = "",
    human_guidance: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    """
    Phase-2 finalization workflow: generate final summary after human approval.

    Triggers:
    - From API: POST /api/v1/tasks/{task_id}/approve-and-finalize
    - User must have approved phase-1 analysis

    State transitions:
    - On entry: task.workflow_state = FINAL_GENERATING (set by API before task dispatch)
    - On success: task.workflow_state = COMPLETED
    - On failure: task.workflow_state = FAILED

    Args:
        owner_id: User ID for authorization
        task_id: Task ID to update
        edited_aggregated_chunk_insights: User-edited analysis (optional)
        human_guidance: User's guidance for final generation
        trace_id: Request correlation ID

    Returns:
        Dict with:
        - workflow_state: COMPLETED on success
        - final_summary: Generated summary text
        - message: Status message
    """
    logger.info(
        f"[WorkflowRuntimeTasks] Starting finalization workflow: task_id={task_id}, owner_id={owner_id}, trace_id={trace_id}"
    )

    db = SessionLocal()
    try:
        orchestration_service = _build_orchestration_service()

        # Run async workflow in event loop
        final_summary = _run_async(
            orchestration_service.start_finalization_workflow_async(
                owner_id=owner_id,
                task_id=task_id,
                edited_aggregated_chunk_insights=edited_aggregated_chunk_insights,
                human_guidance=human_guidance,
                trace_id=trace_id,
            )
        )

        logger.info(
            f"[WorkflowRuntimeTasks] Finalization workflow completed: task_id={task_id}, summary_len={len(final_summary)}"
        )

        return {
            "workflow_state": "COMPLETED",
            "final_summary": final_summary,
            "message": "Phase-2 finalization completed",
        }

    except ValueError as e:
        logger.warning(f"[WorkflowRuntimeTasks] Validation error: {e}")
        raise

    except Exception as e:
        logger.error(f"[WorkflowRuntimeTasks] Finalization workflow failed: {e}", exc_info=True)
        # Retry up to max_retries times
        raise self.retry(exc=e, countdown=60)  # type: ignore

    finally:
        db.close()


@celery_app.task(
    name="backend.tasks.workflow_runtime_tasks.async_execute_time_travel_qa",
    bind=True,
    acks_late=True,
    max_retries=2,
    default_retry_delay=30,
)
def async_execute_time_travel_qa(
    self,
    owner_id: str,
    task_id: str,
    timestamp: str,
    question: str,
    window_seconds: int = 20,
    trace_id: str = "",
) -> dict[str, Any]:
    """
    Time travel Q&A: answer questions based on checkpoint recovery + timestamp context.

    Triggers:
    - Internal async entry for timestamp Q&A execution
    - Can be called at any time after analysis phase completes

    Args:
        owner_id: User ID for authorization
        task_id: Task ID for checkpoint recovery
        timestamp: Target timestamp (HH:MM:SS format)
        question: Question to answer based on timestamp context
        window_seconds: Time window before/after timestamp
        trace_id: Request correlation ID

    Returns:
        Dict with:
        - answer: Answer text with evidence context
        - timestamp: Queried timestamp
        - window_seconds: Evidence window used
        - message: Status message
    """
    logger.info(
        f"[WorkflowRuntimeTasks] Starting time travel Q&A: task_id={task_id}, timestamp={timestamp}, trace_id={trace_id}"
    )

    db = SessionLocal()
    try:
        orchestration_service = _build_orchestration_service()

        # Run async workflow in event loop
        answer = _run_async(
            orchestration_service.start_time_travel_qa_async(
                owner_id=owner_id,
                task_id=task_id,
                timestamp=timestamp,
                question=question,
                window_seconds=window_seconds,
                trace_id=trace_id,
            )
        )

        logger.info(f"[WorkflowRuntimeTasks] Time travel Q&A completed: task_id={task_id}, answer_len={len(answer)}")

        return {
            "answer": answer,
            "timestamp": timestamp,
            "window_seconds": window_seconds,
            "message": "Time travel Q&A completed",
        }

    except ValueError as e:
        logger.warning(f"[WorkflowRuntimeTasks] Validation error: {e}")
        raise

    except Exception as e:
        logger.error(f"[WorkflowRuntimeTasks] Time travel Q&A failed: {e}", exc_info=True)
        # Retry up to max_retries times
        raise self.retry(exc=e, countdown=30)  # type: ignore

    finally:
        db.close()
