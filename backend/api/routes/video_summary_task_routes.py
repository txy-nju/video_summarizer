from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.filters import parse_fields
from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.dependencies import (
    get_video_summary_task_service,
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
)
from backend.services.video_summary_task_service import VideoSummaryTaskService
from backend.services.workflow_orchestration_service import WorkflowOrchestrationService


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
    request: Request,
    payload: StartAnalysisWorkflowRequest | None = None,
    current_user: UserView = Depends(get_current_user),
    task_service: VideoSummaryTaskService = Depends(get_video_summary_task_service),
    workflow_service: WorkflowOrchestrationService = Depends(get_workflow_orchestration_service),
):
    """Trigger phase-1 analysis workflow (async).

    Loads video transcript and keyframes, then dispatches async analysis task.
    Returns immediately with 202 Accepted.

    Idempotency:
    - WAITING_USER_APPROVAL → returns 200 with cached draft_summary.
    - COMPLETED → returns 200 with cached final_summary.
    - FINAL_GENERATING → returns 422 (phase-2 in progress, cannot go back).

    State transition: task.workflow_state = DRAFT_GENERATING (until analysis completes)
    """
    _ = payload

    trace_id = str(getattr(request.state, "trace_id", ""))
    try:
        response_data = task_service.dispatch_start_analysis_workflow(
            owner_id=current_user.user_id,
            task_id=task_id,
            trace_id=trace_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        msg = str(exc)
        if msg == "finalization_in_progress":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Phase-2 finalization is currently in progress. "
                "Cannot restart phase-1 analysis. Please wait for finalization to complete.",
            ) from exc
        raise

    # 仅在任务被真正分发（非幂等命中）时推送 WS 首事件
    if response_data.get("workflow_state") == "DRAFT_GENERATING":
        workflow_service.publish_task_accepted(
            user_id=current_user.user_id,
            task_id=task_id,
            trace_id=trace_id,
        )

    return StartAnalysisWorkflowResponse(
        data=response_data,
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
    _ = workflow_service

    trace_id = str(getattr(request.state, "trace_id", ""))
    try:
        response_data = task_service.dispatch_approve_and_finalize_workflow(
            owner_id=current_user.user_id,
            task_id=task_id,
            edited_aggregated_chunk_insights=payload.edited_aggregated_chunk_insights or "",
            human_guidance=payload.human_guidance or "",
            trace_id=trace_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ApproveAndFinalizeResponse(
        data=response_data,
        meta=_build_meta(request),
    )
