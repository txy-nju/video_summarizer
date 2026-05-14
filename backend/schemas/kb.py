from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.common import MetaInfo, PaginationInfo


class KnowledgeBaseRetrievalConfig(BaseModel):
    top_k: int = Field(ge=1)
    rerank: bool


class KnowledgeBaseToolPreferences(BaseModel):
    allow_web_search: bool


class KnowledgeBaseLlmPolicy(BaseModel):
    temperature: float = Field(ge=0.0, le=2.0)


class KnowledgeBaseConfig(BaseModel):
    retrieval: KnowledgeBaseRetrievalConfig
    tool_preferences: KnowledgeBaseToolPreferences
    llm_policy: KnowledgeBaseLlmPolicy


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    config: KnowledgeBaseConfig


class KnowledgeBaseUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    config: KnowledgeBaseConfig | None = None


class KnowledgeBaseView(BaseModel):
    kbid: str
    owner_id: str
    name: str
    category: str | None = None
    description: str | None = None
    vector_collection_name: str | None = None
    config: KnowledgeBaseConfig
    created_at: datetime


class KnowledgeBaseResponse(BaseModel):
    status: str = "success"
    data: KnowledgeBaseView
    meta: MetaInfo


class KnowledgeBaseListResponse(BaseModel):
    status: str = "success"
    data: list[KnowledgeBaseView]
    pagination: PaginationInfo
    meta: MetaInfo


class KnowledgeBaseDeleteData(BaseModel):
    kbid: str


class KnowledgeBaseDeleteResponse(BaseModel):
    status: str = "success"
    data: KnowledgeBaseDeleteData
    meta: MetaInfo


class KnowledgeBaseVideoBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str = Field(min_length=1, max_length=64)


class KnowledgeBaseVideoItem(BaseModel):
    video_id: str
    file_name: str
    created_at: datetime


class KnowledgeBaseVideoBindData(BaseModel):
    kbid: str
    video_id: str


class KnowledgeBaseVideoBindResponse(BaseModel):
    status: str = "success"
    data: KnowledgeBaseVideoBindData
    meta: MetaInfo


class KnowledgeBaseVideoListResponse(BaseModel):
    status: str = "success"
    data: list[KnowledgeBaseVideoItem]
    pagination: PaginationInfo
    meta: MetaInfo


class KnowledgeBaseVideoRemoveResponse(BaseModel):
    status: str = "success"
    data: KnowledgeBaseVideoBindData
    meta: MetaInfo
