from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import MetaInfo, PaginationInfo


WorkflowState = Literal[
    "DRAFT_GENERATING",
    "WAITING_USER_APPROVAL",
    "FINAL_GENERATING",
    "COMPLETED",
]


class VideoSummaryTaskCreateRequest(BaseModel):
    kbid: str = Field(min_length=1, max_length=64)
    video_id: str = Field(min_length=1, max_length=64)
    user_initial_preference: str | None = Field(default=None, max_length=5000)


class VideoSummaryTaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_summary: str | None = Field(default=None, max_length=20000)
    user_guidance: str | None = Field(default=None, max_length=10000)
    title: str | None = Field(default=None, max_length=255)


class VideoSummaryTaskView(BaseModel):
    task_id: str
    kbid: str
    video_id: str
    workflow_state: WorkflowState = "DRAFT_GENERATING"
    user_initial_preference: str | None = None
    draft_summary: str | None = None
    user_guidance: str | None = None
    final_summary: str | None = None
    title: str | None = None
    summary_vector_ids: list[str] | None = None
    created_at: datetime
    updated_at: datetime


class VideoSummaryTaskResponse(BaseModel):
    status: str = "success"
    data: VideoSummaryTaskView
    meta: MetaInfo


class VideoSummaryTaskListResponse(BaseModel):
    status: str = "success"
    data: list[VideoSummaryTaskView]
    pagination: PaginationInfo
    meta: MetaInfo


class VideoSummaryTaskDeleteData(BaseModel):
    task_id: str


class VideoSummaryTaskDeleteResponse(BaseModel):
    status: str = "success"
    data: VideoSummaryTaskDeleteData
    meta: MetaInfo
