from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from backend.api.filters import parse_fields
from backend.auth.dependencies import get_current_user
from backend.auth.models import UserView
from backend.dependencies import get_video_resource_service
from backend.schemas.common import MetaInfo, PaginationInfo
from backend.schemas.video_resource import (
    VideoResourceCreateRequest,
    VideoResourceDeleteData,
    VideoResourceDeleteResponse,
    VideoResourceListResponse,
    VideoResourceResponse,
    VideoResourceUpdateRequest,
)
from backend.services.video_resource_service import VideoResourceService


router = APIRouter(prefix="/api/v1/videos", tags=["video-resources"])
_ALLOWED_FIELDS = {
    "video_id",
    "owner_id",
    "file_name",
    "oss_key",
    "duration",
    "transcribe_status",
    "frame_extraction_status",
    "extract_completed_at",
    "created_at",
}


def _build_meta(request: Request) -> MetaInfo:
    return MetaInfo(request_id=getattr(request.state, "request_id", "-"))


@router.post("", response_model=VideoResourceResponse, status_code=status.HTTP_201_CREATED)
async def create_video_resource(
    payload: VideoResourceCreateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    video_service: VideoResourceService = Depends(get_video_resource_service),
):
    video = video_service.create_video_resource(owner_id=current_user.user_id, payload=payload)
    return VideoResourceResponse(data=video, meta=_build_meta(request))


@router.get("", response_model=VideoResourceListResponse)
async def list_video_resources(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    fields: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    current_user: UserView = Depends(get_current_user),
    video_service: VideoResourceService = Depends(get_video_resource_service),
):
    _ = sort, cursor
    if fields is not None:
        try:
            parse_fields(fields, _ALLOWED_FIELDS)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    items, pagination = video_service.list_video_resources(owner_id=current_user.user_id, page=page, page_size=page_size)
    return VideoResourceListResponse(data=items, pagination=PaginationInfo.model_validate(pagination), meta=_build_meta(request))


@router.get("/{video_id}", response_model=VideoResourceResponse)
async def get_video_resource(
    video_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    video_service: VideoResourceService = Depends(get_video_resource_service),
):
    video = video_service.get_video_resource(owner_id=current_user.user_id, video_id=video_id)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video resource not found")
    return VideoResourceResponse(data=video, meta=_build_meta(request))


@router.patch("/{video_id}", response_model=VideoResourceResponse)
async def update_video_resource(
    video_id: str,
    payload: VideoResourceUpdateRequest,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    video_service: VideoResourceService = Depends(get_video_resource_service),
):
    video = video_service.update_video_resource(owner_id=current_user.user_id, video_id=video_id, payload=payload)
    if video is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video resource not found")
    return VideoResourceResponse(data=video, meta=_build_meta(request))


@router.delete("/{video_id}", response_model=VideoResourceDeleteResponse, status_code=status.HTTP_202_ACCEPTED)
async def delete_video_resource(
    video_id: str,
    request: Request,
    current_user: UserView = Depends(get_current_user),
    video_service: VideoResourceService = Depends(get_video_resource_service),
):
    deleted = video_service.delete_video_resource(owner_id=current_user.user_id, video_id=video_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video resource not found")
    return VideoResourceDeleteResponse(data=VideoResourceDeleteData(video_id=video_id), meta=_build_meta(request))
