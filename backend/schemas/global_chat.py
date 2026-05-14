from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import MetaInfo, PaginationInfo


class CitedSource(BaseModel):
    """引用来源（格式遵循全局约定）"""
    video_id: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    time_range: str = Field(min_length=1, max_length=50)  # "00:10:00-00:11:00"
    quote: str = Field(min_length=1, max_length=1000)
    score: float = Field(ge=0, le=1)


class AttachmentInfo(BaseModel):
    """多模态附件元数据"""
    name: str = Field(min_length=1, max_length=255)
    oss_key: str = Field(min_length=1, max_length=1024)
    mime_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=0)


class GlobalChatSessionCreateRequest(BaseModel):
    """创建新的全局知识库会话"""
    kbid: str = Field(min_length=1, max_length=64)
    chat_title: str | None = Field(default=None, max_length=255)


class GlobalChatSessionUpdateRequest(BaseModel):
    """更新会话标题"""
    model_config = ConfigDict(extra="forbid")

    chat_title: str = Field(min_length=1, max_length=255)


class GlobalChatSessionView(BaseModel):
    """全局知识库会话视图"""
    chat_id: str
    kbid: str
    chat_title: str
    created_at: datetime


class GlobalChatSessionResponse(BaseModel):
    status: str = "success"
    data: GlobalChatSessionView
    meta: MetaInfo


class GlobalChatSessionListResponse(BaseModel):
    status: str = "success"
    data: list[GlobalChatSessionView]
    pagination: PaginationInfo
    meta: MetaInfo


class GlobalChatSessionDeleteData(BaseModel):
    chat_id: str


class GlobalChatSessionDeleteResponse(BaseModel):
    status: str = "success"
    data: GlobalChatSessionDeleteData
    meta: MetaInfo


# ============================================
# Global QA Record Schemas
# ============================================


class GlobalQARecordCreateRequest(BaseModel):
    """提问请求"""
    question_content: str = Field(min_length=1, max_length=5000)
    attachments: list[AttachmentInfo] = Field(default_factory=list, max_length=10)


class GlobalQARecordUpdateRequest(BaseModel):
    """重新生成回答（特殊更新）"""
    model_config = ConfigDict(extra="forbid")

    regenerate: bool = Field(default=False)


class GlobalQARecordView(BaseModel):
    """全局跨文档问答记录视图"""
    qa_id: str
    chat_id: str
    question_content: str
    answer_content: str | None = None
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    cited_sources: list[CitedSource] = Field(default_factory=list)
    question_time: datetime


class GlobalQARecordResponse(BaseModel):
    status: str = "success"
    data: GlobalQARecordView
    meta: MetaInfo


class GlobalQARecordListResponse(BaseModel):
    status: str = "success"
    data: list[GlobalQARecordView]
    pagination: PaginationInfo
    meta: MetaInfo


class GlobalQARecordDeleteData(BaseModel):
    qa_id: str


class GlobalQARecordDeleteResponse(BaseModel):
    status: str = "success"
    data: GlobalQARecordDeleteData
    meta: MetaInfo
