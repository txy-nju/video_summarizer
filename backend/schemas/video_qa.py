from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from backend.schemas.common import MetaInfo, PaginationInfo
from backend.schemas.global_chat import AttachmentInfo, CitedSource


class VideoQARecordCreateRequest(BaseModel):
    """创建单视频局部追问"""
    task_id: str = Field(min_length=1, max_length=64)
    start_time: str = Field(min_length=1, max_length=20)  # HH:MM:SS format
    end_time: str = Field(min_length=1, max_length=20)
    question_content: str = Field(min_length=0, max_length=5000)
    attachments: list[AttachmentInfo] = Field(default_factory=list, max_length=10)


class VideoQARecordUpdateRequest(BaseModel):
    """重新生成回答（特殊更新）"""
    model_config = ConfigDict(extra="forbid")

    # 暂时仅支持"重生成"意图，可扩展为明确的参数
    regenerate: bool = Field(default=False)


class TimeTravelQAStreamRequest(BaseModel):
    """时间旅行问答流式请求（兼容 question 与 question_content 入参）"""

    model_config = ConfigDict(extra="forbid")

    timestamp: str = Field(pattern=r"^\d{2}:\d{2}:\d{2}$", description="Target timestamp (HH:MM:SS)")
    question_content: str = Field(
        min_length=0,
        max_length=5000,
        validation_alias=AliasChoices("question", "question_content"),
    )
    attachments: list[AttachmentInfo] = Field(default_factory=list, max_length=10)
    # window_seconds omitted => route falls back to RAG streaming path
    window_seconds: int | None = Field(default=None, ge=5, le=300, description="Evidence window in seconds")


class VideoQARecordView(BaseModel):
    """单视频追问记录视图"""
    qa_id: str
    task_id: str
    start_time: str
    end_time: str
    question_content: str
    answer_content: str | None = None
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    cited_sources: list[CitedSource] = Field(default_factory=list)
    question_time: datetime


class VideoQARecordResponse(BaseModel):
    status: str = "success"
    data: VideoQARecordView
    meta: MetaInfo


class VideoQARecordListResponse(BaseModel):
    status: str = "success"
    data: list[VideoQARecordView]
    pagination: PaginationInfo
    meta: MetaInfo


class VideoQARecordDeleteData(BaseModel):
    qa_id: str


class VideoQARecordDeleteResponse(BaseModel):
    status: str = "success"
    data: VideoQARecordDeleteData
    meta: MetaInfo
