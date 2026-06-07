"""add created_at to video_resources

Revision ID: 010_video_resource_created_at
Revises: 009_add_file_hash_and_ref_count
Create Date: 2026-06-06 20:00:00

为 VideoResource 新增 created_at 列，支持按上传时间排序。
存量行回填 extract_completed_at（如有），否则填 NOW()。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "010_video_resource_created_at"
down_revision: Union[str, None] = "009_add_file_hash_and_ref_count"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "video_resources",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    # 回填：优先用 extract_completed_at，否则用当前时间
    op.execute(
        sa.text(
            "UPDATE video_resources "
            "SET created_at = COALESCE(extract_completed_at, NOW())"
        )
    )
    # 回填后将列改为 NOT NULL
    op.alter_column("video_resources", "created_at", nullable=False)


def downgrade() -> None:
    op.drop_column("video_resources", "created_at")
