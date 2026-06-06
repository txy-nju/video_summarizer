from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Column, DateTime, Enum as SqlEnum, ForeignKey, String, Table, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates

from backend.models.enums import FrameExtractionStatus, TranscribeStatus, WorkflowState


class RetrievalConfigSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(ge=1)
    rerank: bool


class ToolPreferencesSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_web_search: bool


class LlmPolicySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(ge=0.0, le=2.0)


class KnowledgeBaseConfigSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieval: RetrievalConfigSchema
    tool_preferences: ToolPreferencesSchema
    llm_policy: LlmPolicySchema


class KeyframeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: str
    scene_change_score: float = Field(ge=0.0, le=1.0)
    scene_change_level: str
    oss_key: str


class AttachmentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    oss_key: str
    mime_type: str
    size_bytes: int = Field(ge=0)


class CitedSourceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str
    task_id: str | None = None
    video_name: str | None = None
    time_range: str
    quote: str
    score: float = Field(ge=0.0, le=1.0)


def _validate_model(value: dict, schema_cls: type[BaseModel], field_name: str) -> dict:
    try:
        return schema_cls.model_validate(value).model_dump()
    except ValidationError as exc:
        raise ValueError(f"Invalid JSON payload for {field_name}: {exc}") from exc


def _validate_model_list(value: list, schema_cls: type[BaseModel], field_name: str) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")

    validated: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"Each item in {field_name} must be an object")
        validated.append(_validate_model(item, schema_cls, field_name))
    return validated


def _validate_vector_ids(value: dict | list | None, field_name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return value


class Base(DeclarativeBase):
    pass


def _uuid_str() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class VideoResource(Base):
    __tablename__ = "video_resources"

    video_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    oss_key: Mapped[str | None] = mapped_column(String(512))
    duration: Mapped[int | None] = mapped_column()
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    full_transcript: Mapped[str | None] = mapped_column(Text)
    transcript_segments: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    transcribe_status: Mapped[TranscribeStatus] = mapped_column(
        SqlEnum(TranscribeStatus, name="transcribe_status"),
        default=TranscribeStatus.UPLOADED,
        nullable=False,
    )
    transcript_vector_ids: Mapped[dict | list | None] = mapped_column(JSONB)

    keyframes: Mapped[dict | list | None] = mapped_column(JSONB)
    frame_extraction_status: Mapped[FrameExtractionStatus] = mapped_column(
        SqlEnum(FrameExtractionStatus, name="frame_extraction_status"),
        default=FrameExtractionStatus.UPLOADED,
        nullable=False,
    )
    keyframes_oss_prefix: Mapped[str | None] = mapped_column(String(512))
    extract_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_status: Mapped[str] = mapped_column(String(32), default="NONE", nullable=False)

    # 自愈恢复追踪（Celery Beat 周期扫描使用，非 Celery 自身重试计数）
    recovery_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    last_recovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Hash-based dedup & reference counting
    task_ref_count: Mapped[int] = mapped_column(default=0, nullable=False)

    @validates("transcript_vector_ids")
    def validate_transcript_vector_ids(self, key: str, value: dict | list | None) -> list[str] | None:
        return _validate_vector_ids(value, key)

    @validates("keyframes")
    def validate_keyframes(self, key: str, value: dict | list | None) -> list[dict] | None:
        if value is None:
            return None
        validated = _validate_model_list(value, KeyframeSchema, key)
        return validated


# Many-to-Many Join Table: Knowledge Base <-> Video Resource (implicit, non-ORM table)
kb_video_relation_table = Table(
    "kb_video_relations",
    Base.metadata,
    Column("kbid", String(36), ForeignKey("knowledge_bases.kbid", ondelete="CASCADE"), primary_key=True),
    Column("video_id", String(36), ForeignKey("video_resources.video_id", ondelete="CASCADE"), primary_key=True),
    UniqueConstraint("kbid", "video_id", name="uq_kb_video"),
)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    kbid: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    vector_collection_name: Mapped[str | None] = mapped_column(String(255))
    config: Mapped[dict | None] = mapped_column(JSONB)

    # M:N relationship with VideoResource via implicit join table
    videos: Mapped[list["VideoResource"]] = relationship(
        "VideoResource",
        secondary=kb_video_relation_table,
        backref="knowledge_bases",
        lazy="noload",
    )

    @validates("config")
    def validate_config(self, key: str, value: dict | None) -> dict | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be an object")
        return _validate_model(value, KnowledgeBaseConfigSchema, key)


class VideoSummaryTask(Base):
    __tablename__ = "video_summary_tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    kbid: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.kbid"), nullable=False, index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("video_resources.video_id"), nullable=False, index=True)
    workflow_state: Mapped[WorkflowState] = mapped_column(
        SqlEnum(WorkflowState, name="workflow_state"),
        default=WorkflowState.DRAFT_GENERATING,
        nullable=False,
    )
    user_initial_preference: Mapped[str | None] = mapped_column(Text)
    draft_summary: Mapped[str | None] = mapped_column(Text)
    user_guidance: Mapped[str | None] = mapped_column(Text)
    final_summary: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(255))
    summary_vector_ids: Mapped[dict | list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @validates("summary_vector_ids")
    def validate_summary_vector_ids(self, key: str, value: dict | list | None) -> list[str] | None:
        return _validate_vector_ids(value, key)


class VideoQARecord(Base):
    __tablename__ = "video_qa_records"

    qa_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("video_summary_tasks.task_id"), nullable=False, index=True)
    start_time: Mapped[str | None] = mapped_column(String(32))
    end_time: Mapped[str | None] = mapped_column(String(32))
    question_content: Mapped[str] = mapped_column(Text, nullable=False)
    answer_content: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[dict | list | None] = mapped_column(JSONB)
    cited_sources: Mapped[dict | list | None] = mapped_column(JSONB)
    question_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @validates("attachments")
    def validate_attachments(self, key: str, value: dict | list | None) -> list[dict] | None:
        if value is None:
            return None
        return _validate_model_list(value, AttachmentSchema, key)

    @validates("cited_sources")
    def validate_cited_sources(self, key: str, value: dict | list | None) -> list[dict] | None:
        if value is None:
            return None
        return _validate_model_list(value, CitedSourceSchema, key)


class GlobalChatSession(Base):
    __tablename__ = "global_chat_sessions"

    chat_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    kbid: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.kbid"), nullable=False, index=True)
    chat_title: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class GlobalQARecord(Base):
    __tablename__ = "global_qa_records"

    qa_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    chat_id: Mapped[str] = mapped_column(ForeignKey("global_chat_sessions.chat_id"), nullable=False, index=True)
    question_content: Mapped[str] = mapped_column(Text, nullable=False)
    answer_content: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[dict | list | None] = mapped_column(JSONB)
    cited_sources: Mapped[dict | list | None] = mapped_column(JSONB)
    question_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @validates("attachments")
    def validate_attachments(self, key: str, value: dict | list | None) -> list[dict] | None:
        if value is None:
            return None
        return _validate_model_list(value, AttachmentSchema, key)

    @validates("cited_sources")
    def validate_cited_sources(self, key: str, value: dict | list | None) -> list[dict] | None:
        if value is None:
            return None
        return _validate_model_list(value, CitedSourceSchema, key)


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    device_token_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    device_token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(32))
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
