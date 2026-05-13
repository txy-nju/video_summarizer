from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.models.enums import FrameExtractionStatus, TranscribeStatus, WorkflowState


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

    full_transcript: Mapped[str | None] = mapped_column(Text)
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


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    kbid: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    vector_collection_name: Mapped[str | None] = mapped_column(String(255))
    config: Mapped[dict | None] = mapped_column(JSONB)


class KBVideoRelation(Base):
    __tablename__ = "kb_video_relations"
    __table_args__ = (UniqueConstraint("kbid", "video_id", name="uq_kb_video"),)

    relation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    kbid: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.kbid"), nullable=False)
    video_id: Mapped[str] = mapped_column(ForeignKey("video_resources.video_id"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


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


class VideoQARecord(Base):
    __tablename__ = "video_qa_records"

    qa_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("video_summary_tasks.task_id"), nullable=False, index=True)
    start_time: Mapped[str | None] = mapped_column(String(32))
    end_time: Mapped[str | None] = mapped_column(String(32))
    question_content: Mapped[str] = mapped_column(Text, nullable=False)
    answer_content: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[dict | list | None] = mapped_column(JSONB)
    question_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


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
