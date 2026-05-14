from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.common import MetaInfo, PaginationInfo


VideoExtractStatus = Literal["UPLOADED", "TRANSCRIBING", "EXTRACTING", "COMPLETED", "FAILED"]


class KeyFrameItem(BaseModel):
    time: str
    scene_change_score: float = Field(ge=0.0, le=1.0)
    scene_change_level: str
    oss_key: str


class VideoResourceCreateRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    oss_key: str = Field(min_length=1, max_length=2000)
    duration: int = Field(ge=0)


class VideoResourceUpdateRequest(BaseModel):
    file_name: str | None = Field(default=None, min_length=1, max_length=255)
    duration: int | None = Field(default=None, ge=0)


class VideoResourceView(BaseModel):
    video_id: str
    owner_id: str
    file_name: str
    oss_key: str
    duration: int
    full_transcript: str | None = None
    transcribe_status: VideoExtractStatus = "UPLOADED"
    transcript_vector_ids: list[str] | None = None
    keyframes: list[KeyFrameItem] | None = None
    frame_extraction_status: VideoExtractStatus = "UPLOADED"
    keyframes_oss_prefix: str | None = None
    extract_completed_at: datetime | None = None
    created_at: datetime


class VideoResourceResponse(BaseModel):
    status: str = "success"
    data: VideoResourceView
    meta: MetaInfo


class VideoResourceListResponse(BaseModel):
    status: str = "success"
    data: list[VideoResourceView]
    pagination: PaginationInfo
    meta: MetaInfo


class VideoResourceDeleteData(BaseModel):
    video_id: str


class VideoResourceDeleteResponse(BaseModel):
    status: str = "success"
    data: VideoResourceDeleteData
    meta: MetaInfo
