from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from backend.api.filters import parse_fields
from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.dependencies import (
    get_video_summary_task_service,
    get_video_qa_repository,
    get_workflow_orchestration_service,
)
from backend.schemas.common import MetaInfo, PaginationInfo
from backend.schemas.video_summary_task import (
    VideoSummaryTaskCreateRequest,
    VideoSummaryTaskDeleteData,
    VideoSummaryTaskDeleteResponse,
    VideoSummaryTaskListResponse,
    VideoSummaryTaskResponse,
    VideoSummaryTaskUpdateRequest,
    StartAnalysisWorkflowRequest,
    StartAnalysisWorkflowResponse,
    ApproveAndFinalizeRequest,
    ApproveAndFinalizeResponse,
    TimeTravelQARequest,
)
from backend.services.video_summary_task_service import VideoSummaryTaskService
from backend.services.workflow_orchestration_service import WorkflowOrchestrationService
from backend.repositories.video_qa_repository import VideoQARepository


router = APIRouter(prefix="/api/v1/tasks", tags=["video-summary-tasks"])
_ALLOWED_FIELDS = {
    "task_id",
    "kbid",
    "video_id",
    "workflow_state",
    "user_initial_preference",
    "draft_summary",
    "user_guidance",
    "final_summary",
    "title",
    "summary_vector_ids",
    "created_at",
    "updated_at",
}


def _build_meta(request: Request) -> MetaInfo:
    return MetaInfo(request_id=getattr(request.state, "request_id", "-"))


def _seconds_to_hms(total_seconds: int) -> str:
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _compute_time_window(timestamp: str, window_seconds: int) -> tuple[str, str]:
    hours, minutes, seconds = (int(part) for part in timestamp.split(":"))
    center_seconds = hours * 3600 + minutes * 60 + seconds
    half_window = max(window_seconds // 2, 0)
    start_seconds = max(center_seconds - half_window, 0)
    end_seconds = center_seconds + half_window
    return _seconds_to_hms(start_seconds), _seconds_to_hms(end_seconds)


def _chunk_text(text: str, chunk_size: int = 64) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def _sse_event(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("", response_model=VideoSummaryTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_video_summary_task(
    payload: VideoSummaryTaskCreateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
):
    try:
        task = task_service.create_video_summary_task(owner_id=current_user.user_id, payload=payload)
    except ValueError as exc:
        if str(exc) == "video_not_ready":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Video is not ready for summarization. Transcription and keyframe extraction must both complete first.",
            )
        raise
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base or video resource not found",
        )
    return VideoSummaryTaskResponse(data=task, meta=_build_meta(request))


@router.get("", response_model=VideoSummaryTaskListResponse)
async def list_video_summary_tasks(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    fields: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
):
    _ = sort, cursor
    if fields is not None:
        try:
            parse_fields(fields, _ALLOWED_FIELDS)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    items, pagination = task_service.list_video_summary_tasks(
        owner_id=current_user.user_id,
        page=page,
        page_size=page_size,
    )
    return VideoSummaryTaskListResponse(data=items, pagination=PaginationInfo.model_validate(pagination), meta=_build_meta(request))


@router.get("/{task_id}", response_model=VideoSummaryTaskResponse)
async def get_video_summary_task(
    task_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
):
    task = task_service.get_video_summary_task(owner_id=current_user.user_id, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video summary task not found")
    return VideoSummaryTaskResponse(data=task, meta=_build_meta(request))


@router.patch("/{task_id}", response_model=VideoSummaryTaskResponse)
async def update_video_summary_task(
    task_id: str,
    payload: VideoSummaryTaskUpdateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
):
    task = task_service.update_video_summary_task(owner_id=current_user.user_id, task_id=task_id, payload=payload)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video summary task not found")
    return VideoSummaryTaskResponse(data=task, meta=_build_meta(request))


@router.delete("/{task_id}", response_model=VideoSummaryTaskDeleteResponse)
async def delete_video_summary_task(
    task_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
):
    deleted = task_service.delete_video_summary_task(owner_id=current_user.user_id, task_id=task_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video summary task not found")
    return VideoSummaryTaskDeleteResponse(data=VideoSummaryTaskDeleteData(task_id=task_id), meta=_build_meta(request))


# ==================== Workflow Endpoints ====================


@router.post("/{task_id}/start-analysis", response_model=StartAnalysisWorkflowResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_analysis_workflow(
    task_id: str,
    payload: StartAnalysisWorkflowRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
    workflow_service: WorkflowOrchestrationService = Depends(get_workflow_orchestration_service),
):
    """Trigger phase-1 analysis workflow (async).

    Loads video transcript and keyframes, then dispatches async analysis task.
    Returns immediately with 202 Accepted.

    State transition: task.workflow_state = DRAFT_GENERATING (until analysis completes)
    """
    from backend.dependencies import get_video_resource_repository
    
    _ = payload  # payload currently unused but reserved for future extensibility

    # Get task to verify existence and permissions
    task = task_service.get_video_summary_task(owner_id=current_user.user_id, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video summary task not found")

    # Get video resource to load transcript and keyframes
    video_repo = get_video_resource_repository()
    video = video_repo.get_by_owner_and_id(owner_id=current_user.user_id, video_id=task.video_id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video resource not found")

    # Load transcript and keyframes
    transcript = video.full_transcript or ""
    keyframes = video.keyframes or []

    # Get trace ID for correlation
    trace_id = str(getattr(request.state, "request_id", ""))

    # Dispatch async workflow task
    from backend.tasks.workflow_runtime_tasks import async_execute_analysis_workflow

    task_result = async_execute_analysis_workflow.apply_async(
        args=[
            current_user.user_id,
            task_id,
            transcript,
            keyframes,
            task.user_initial_preference or "",
            trace_id,
        ],
        queue="default",
    )

    return StartAnalysisWorkflowResponse(
        data={
            "task_id": task_id,
            "celery_task_id": task_result.id,
            "thread_id": task_id,
            "workflow_state": "DRAFT_GENERATING",
            "accepted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "message": "Phase-1 analysis workflow dispatched",
        },
        meta=_build_meta(request),
    )


@router.post("/{task_id}/approve-and-finalize", response_model=ApproveAndFinalizeResponse, status_code=status.HTTP_202_ACCEPTED)
async def approve_and_finalize_workflow(
    task_id: str,
    payload: ApproveAndFinalizeRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
    workflow_service: WorkflowOrchestrationService = Depends(get_workflow_orchestration_service),
):
    """Approve phase-1 analysis and trigger phase-2 finalization (async).

    User provides edited analysis and guidance, then async finalization runs.
    Returns immediately with 202 Accepted.

    Precondition: task.workflow_state must be WAITING_USER_APPROVAL
    State transition: task.workflow_state = FINAL_GENERATING (until finalization completes)
    """
    # Get task to verify existence and permissions
    task = task_service.get_video_summary_task(owner_id=current_user.user_id, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video summary task not found")

    # Verify task is in approval gate
    if task.workflow_state != "WAITING_USER_APPROVAL":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Task must be in WAITING_USER_APPROVAL state, got {task.workflow_state}",
        )

    # Get trace ID for correlation
    trace_id = str(getattr(request.state, "request_id", ""))

    # Dispatch async finalization task
    from backend.tasks.workflow_runtime_tasks import async_execute_finalization_workflow

    task_result = async_execute_finalization_workflow.apply_async(
        args=[
            current_user.user_id,
            task_id,
            payload.edited_aggregated_chunk_insights or "",
            payload.human_guidance or "",
            trace_id,
        ],
        queue="default",
    )

    return ApproveAndFinalizeResponse(
        data={
            "task_id": task_id,
            "celery_task_id": task_result.id,
            "thread_id": task_id,
            "workflow_state": "FINAL_GENERATING",
            "accepted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "message": "Phase-2 finalization workflow dispatched",
        },
        meta=_build_meta(request),
    )


@router.post("/{task_id}/time-travel-qa/stream", status_code=status.HTTP_200_OK)
async def time_travel_qa_stream(
    task_id: str,
    payload: TimeTravelQARequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
    workflow_service: WorkflowOrchestrationService = Depends(get_workflow_orchestration_service),
    qa_repository: VideoQARepository = Depends(get_video_qa_repository),
):
    """SSE stream output for time travel Q&A result."""
    task = task_service.get_video_summary_task(owner_id=current_user.user_id, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video summary task not found")

    if task.workflow_state not in ("WAITING_USER_APPROVAL", "FINAL_GENERATING", "COMPLETED"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Task must have completed analysis phase to support time travel Q&A",
        )

    trace_id = str(getattr(request.state, "request_id", ""))

    try:
        answer = await workflow_service.start_time_travel_qa_async(
            owner_id=current_user.user_id,
            task_id=task_id,
            timestamp=payload.timestamp,
            question=payload.question,
            window_seconds=payload.window_seconds,
            trace_id=trace_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Time travel Q&A failed: {str(e)}")

    start_time, end_time = _compute_time_window(payload.timestamp, payload.window_seconds)
    qa_record = qa_repository.create(
        owner_id=current_user.user_id,
        task_id=task_id,
        start_time=start_time,
        end_time=end_time,
        question_content=payload.question,
        attachments=[],
    )
    qa_repository.update_answer_by_owner_task_and_qa_id(
        current_user.user_id,
        task_id,
        qa_record.qa_id,
        answer,
    )

    produced_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def _event_iter() -> Iterator[str]:
        try:
            yield _sse_event(
                "start",
                {
                    "task_id": task_id,
                    "qa_id": qa_record.qa_id,
                    "timestamp": produced_at,
                },
            )
            for seq, chunk in enumerate(_chunk_text(answer), start=1):
                yield _sse_event(
                    "delta",
                    {
                        "task_id": task_id,
                        "qa_id": qa_record.qa_id,
                        "chunk": chunk,
                        "sequence": seq,
                        "timestamp": produced_at,
                    },
                )
            yield _sse_event(
                "done",
                {
                    "task_id": task_id,
                    "qa_id": qa_record.qa_id,
                    "answer_content": answer,
                    "timestamp": produced_at,
                },
            )
        except Exception as exc:
            yield _sse_event(
                "error",
                {
                    "task_id": task_id,
                    "qa_id": qa_record.qa_id,
                    "message": str(exc),
                    "timestamp": produced_at,
                },
            )

    return StreamingResponse(
        _event_iter(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
