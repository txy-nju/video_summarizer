from __future__ import annotations

from datetime import datetime
from typing import Literal, Any

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import MetaInfo, PaginationInfo


WorkflowState = Literal[
    "DRAFT_GENERATING",
    "WAITING_USER_APPROVAL",
    "FINAL_GENERATING",
    "COMPLETED",
    "FAILED",
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


# Workflow trigger DTO
class StartAnalysisWorkflowRequest(BaseModel):
    """Trigger phase-1 analysis workflow."""
    model_config = ConfigDict(extra="forbid")


class StartAnalysisWorkflowResponse(BaseModel):
    """Response when phase-1 analysis is triggered."""
    status: str = "success"
    data: dict[str, Any] = Field(
        description={
            "thread_id": "Checkpoint recovery ID",
            "workflow_state": "DRAFT_GENERATING",
            "message": "Analysis workflow started",
        }
    )
    meta: MetaInfo


# Workflow approval DTO
class ApproveAndFinalizeRequest(BaseModel):
    """Approve phase-1 analysis and trigger phase-2 finalization."""
    model_config = ConfigDict(extra="forbid")

    edited_aggregated_chunk_insights: str | None = Field(default=None, max_length=20000)
    human_guidance: str | None = Field(default=None, max_length=10000)


class ApproveAndFinalizeResponse(BaseModel):
    """Response when finalization workflow completes."""
    status: str = "success"
    data: dict[str, Any] = Field(
        description={
            "workflow_state": "COMPLETED",
            "final_summary": "Generated summary text",
            "message": "Phase-2 finalization completed",
        }
    )
    meta: MetaInfo


# Time travel Q&A DTO
class TimeTravelQARequest(BaseModel):
    """Ask a question about a specific timestamp in the video."""
    model_config = ConfigDict(extra="forbid")

    timestamp: str = Field(pattern=r"^\d{2}:\d{2}:\d{2}$", description="Target timestamp (HH:MM:SS)")
    question: str = Field(min_length=1, max_length=5000)
    window_seconds: int = Field(default=20, ge=5, le=300, description="Evidence window in seconds")


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
