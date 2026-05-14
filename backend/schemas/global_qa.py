from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import MetaInfo, PaginationInfo
from backend.schemas.global_chat import AttachmentInfo, CitedSource


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
