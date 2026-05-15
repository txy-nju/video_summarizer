"""core assets domain

Revision ID: 001_core_assets_domain
Revises:
Create Date: 2026-05-13 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "001_core_assets_domain"
down_revision = None
branch_labels = None
depends_on = None


transcribe_status_enum = sa.Enum(
    "UPLOADED",
    "TRANSCRIBING",
    "COMPLETED",
    "FAILED",
    name="transcribe_status",
)

frame_extraction_status_enum = sa.Enum(
    "UPLOADED",
    "EXTRACTING",
    "COMPLETED",
    "FAILED",
    name="frame_extraction_status",
)


def upgrade() -> None:
    bind = op.get_bind()
    transcribe_status_enum.create(bind, checkfirst=True)
    frame_extraction_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=36), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True),
        sa.Column("password", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "video_resources",
        sa.Column("video_id", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.String(length=36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("oss_key", sa.String(length=512), nullable=True),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("full_transcript", sa.Text(), nullable=True),
        sa.Column("transcribe_status", transcribe_status_enum, nullable=False, server_default="UPLOADED"),
        sa.Column("transcript_vector_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("keyframes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("frame_extraction_status", frame_extraction_status_enum, nullable=False, server_default="UPLOADED"),
        sa.Column("keyframes_oss_prefix", sa.String(length=512), nullable=True),
        sa.Column("extract_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_video_resources_owner_id", "video_resources", ["owner_id"], unique=False)

    op.create_table(
        "knowledge_bases",
        sa.Column("kbid", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.String(length=36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("vector_collection_name", sa.String(length=255), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_knowledge_bases_owner_id", "knowledge_bases", ["owner_id"], unique=False)

    op.create_table(
        "kb_video_relations",
        sa.Column(
            "kbid",
            sa.String(length=36),
            sa.ForeignKey("knowledge_bases.kbid", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "video_id",
            sa.String(length=36),
            sa.ForeignKey("video_resources.video_id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.UniqueConstraint("kbid", "video_id", name="uq_kb_video"),
    )


def downgrade() -> None:
    op.drop_table("kb_video_relations")
    op.drop_index("ix_knowledge_bases_owner_id", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.drop_index("ix_video_resources_owner_id", table_name="video_resources")
    op.drop_table("video_resources")
    op.drop_table("users")

    bind = op.get_bind()
    frame_extraction_status_enum.drop(bind, checkfirst=True)
    transcribe_status_enum.drop(bind, checkfirst=True)
