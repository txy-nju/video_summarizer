"""asset processing domain

Revision ID: 002_asset_processing_domain
Revises: 001_core_assets_domain
Create Date: 2026-05-13 12:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "002_asset_processing_domain"
down_revision = "001_core_assets_domain"
branch_labels = None
depends_on = None


workflow_state_enum = sa.Enum(
    "DRAFT_GENERATING",
    "WAITING_USER_APPROVAL",
    "FINAL_GENERATING",
    "COMPLETED",
    name="workflow_state",
)


def upgrade() -> None:
    bind = op.get_bind()
    workflow_state_enum.create(bind, checkfirst=True)

    op.create_table(
        "video_summary_tasks",
        sa.Column("task_id", sa.String(length=36), primary_key=True),
        sa.Column("kbid", sa.String(length=36), sa.ForeignKey("knowledge_bases.kbid"), nullable=False),
        sa.Column("video_id", sa.String(length=36), sa.ForeignKey("video_resources.video_id"), nullable=False),
        sa.Column("workflow_state", workflow_state_enum, nullable=False, server_default="DRAFT_GENERATING"),
        sa.Column("user_initial_preference", sa.Text(), nullable=True),
        sa.Column("draft_summary", sa.Text(), nullable=True),
        sa.Column("user_guidance", sa.Text(), nullable=True),
        sa.Column("final_summary", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("summary_vector_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_video_summary_tasks_kbid", "video_summary_tasks", ["kbid"], unique=False)
    op.create_index("ix_video_summary_tasks_video_id", "video_summary_tasks", ["video_id"], unique=False)

    op.create_table(
        "video_qa_records",
        sa.Column("qa_id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("video_summary_tasks.task_id"), nullable=False),
        sa.Column("start_time", sa.String(length=32), nullable=True),
        sa.Column("end_time", sa.String(length=32), nullable=True),
        sa.Column("question_content", sa.Text(), nullable=False),
        sa.Column("answer_content", sa.Text(), nullable=True),
        sa.Column("attachments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("question_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_video_qa_records_task_id", "video_qa_records", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_video_qa_records_task_id", table_name="video_qa_records")
    op.drop_table("video_qa_records")
    op.drop_index("ix_video_summary_tasks_video_id", table_name="video_summary_tasks")
    op.drop_index("ix_video_summary_tasks_kbid", table_name="video_summary_tasks")
    op.drop_table("video_summary_tasks")

    bind = op.get_bind()
    workflow_state_enum.drop(bind, checkfirst=True)
