from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.filters import parse_fields
from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.dependencies import get_video_summary_task_service
from backend.schemas.common import MetaInfo, PaginationInfo
from backend.schemas.video_summary_task import (
    VideoSummaryTaskCreateRequest,
    VideoSummaryTaskDeleteData,
    VideoSummaryTaskDeleteResponse,
    VideoSummaryTaskListResponse,
    VideoSummaryTaskResponse,
    VideoSummaryTaskUpdateRequest,
)
from backend.services.video_summary_task_service import VideoSummaryTaskService


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
    task = task_service.create_video_summary_task(owner_id=current_user.user_id, payload=payload)
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
