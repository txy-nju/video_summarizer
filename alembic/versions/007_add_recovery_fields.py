"""add recovery_attempts and last_recovery_at to video_resources

Revision ID: 007_recovery_fields
Revises: de563b487742
Create Date: 2026-06-02 12:00:00

为自愈恢复机制新增字段：
- recovery_attempts  INTEGER NOT NULL DEFAULT 0  周期扫描触发的恢复次数
- last_recovery_at   TIMESTAMPTZ                 最近一次恢复尝试的时间
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "007_recovery_fields"
down_revision: Union[str, None] = "de563b487742"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # 1. 新增恢复追踪字段（幂等：跳过已存在的列）
    from sqlalchemy import inspect
    conn = op.get_bind()
    inspector = inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("video_resources")}
    if "recovery_attempts" not in existing:
        op.add_column(
            "video_resources",
            sa.Column("recovery_attempts", sa.Integer(), nullable=False, server_default="0"),
        )
    if "last_recovery_at" not in existing:
        op.add_column(
            "video_resources",
            sa.Column("last_recovery_at", sa.DateTime(timezone=True), nullable=True),
        )

    # 2. 为 transcribe_status 和 frame_extraction_status 枚举新增 IRRECOVERABLE 值
    #    使用 IF NOT EXISTS 保证幂等（已在 Python enum 中定义，此处同步 PG 类型）
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TYPE transcribe_status ADD VALUE IF NOT EXISTS 'IRRECOVERABLE';
        EXCEPTION WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TYPE frame_extraction_status ADD VALUE IF NOT EXISTS 'IRRECOVERABLE';
        EXCEPTION WHEN duplicate_object THEN NULL;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_column("video_resources", "last_recovery_at")
    op.drop_column("video_resources", "recovery_attempts")
