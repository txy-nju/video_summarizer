"""add file_hash and task_ref_count to video_resources

Revision ID: 009_add_file_hash_and_ref_count
Revises: 008_video_qa_cited_sources
Create Date: 2026-06-06 18:00:00

为视频去重和引用计数新增字段：
- file_hash       VARCHAR(64)  上传文件的 SHA256 哈希（nullable，存量数据为 NULL）
- task_ref_count  INTEGER      当前引用该视频的 VideoSummaryTask 数量

同时创建部分唯一索引防止并发去重竞态：
- uq_owner_active_file_hash ON video_resources(owner_id, file_hash)
  WHERE is_deleted = FALSE AND file_hash IS NOT NULL
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "009_add_file_hash_and_ref_count"
down_revision: Union[str, None] = "008_video_qa_cited_sources"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # 1. 新增 file_hash 列（nullable，存量数据为 NULL）
    op.add_column(
        "video_resources",
        sa.Column("file_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "idx_video_resources_file_hash",
        "video_resources",
        ["file_hash"],
    )

    # 2. 新增 task_ref_count 列（默认 0）
    op.add_column(
        "video_resources",
        sa.Column("task_ref_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # 3. 回填 task_ref_count：统计每个 video 对应的 task 数量
    op.execute(
        """
        UPDATE video_resources vr
        SET task_ref_count = sub.cnt
        FROM (
            SELECT video_id, COUNT(*) AS cnt
            FROM video_summary_tasks
            GROUP BY video_id
        ) sub
        WHERE vr.video_id = sub.video_id;
        """
    )

    # 4. 创建部分唯一索引：同一用户、同一 hash、未删除 的记录只能有一条
    op.execute(
        """
        CREATE UNIQUE INDEX uq_owner_active_file_hash
        ON video_resources(owner_id, file_hash)
        WHERE is_deleted = FALSE AND file_hash IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_owner_active_file_hash;")
    op.drop_column("video_resources", "task_ref_count")
    op.drop_index("idx_video_resources_file_hash", table_name="video_resources")
    op.drop_column("video_resources", "file_hash")
