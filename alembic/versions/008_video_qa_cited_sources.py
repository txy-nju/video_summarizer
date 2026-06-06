"""add cited_sources to video_qa_records

Revision ID: 008_video_qa_cited_sources
Revises: 007_recovery_fields
Create Date: 2026-06-06 12:00:00

为视频 QA 追问记录新增检索溯源字段：
- cited_sources  JSONB  检索引用来源列表（不含 task_id，QA 本身归属于该任务会话）
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "008_video_qa_cited_sources"
down_revision: Union[str, None] = "007_recovery_fields"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "video_qa_records",
        sa.Column("cited_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("video_qa_records", "cited_sources")
