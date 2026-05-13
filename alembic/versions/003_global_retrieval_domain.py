"""global retrieval domain

Revision ID: 003_global_retrieval_domain
Revises: 002_asset_processing_domain
Create Date: 2026-05-13 12:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "003_global_retrieval_domain"
down_revision = "002_asset_processing_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "global_chat_sessions",
        sa.Column("chat_id", sa.String(length=36), primary_key=True),
        sa.Column("kbid", sa.String(length=36), sa.ForeignKey("knowledge_bases.kbid"), nullable=False),
        sa.Column("chat_title", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_global_chat_sessions_kbid", "global_chat_sessions", ["kbid"], unique=False)

    op.create_table(
        "global_qa_records",
        sa.Column("qa_id", sa.String(length=36), primary_key=True),
        sa.Column("chat_id", sa.String(length=36), sa.ForeignKey("global_chat_sessions.chat_id"), nullable=False),
        sa.Column("question_content", sa.Text(), nullable=False),
        sa.Column("answer_content", sa.Text(), nullable=True),
        sa.Column("attachments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cited_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("question_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_global_qa_records_chat_id", "global_qa_records", ["chat_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_global_qa_records_chat_id", table_name="global_qa_records")
    op.drop_table("global_qa_records")
    op.drop_index("ix_global_chat_sessions_kbid", table_name="global_chat_sessions")
    op.drop_table("global_chat_sessions")
