"""mobile upload and device tokens

Revision ID: 004_mobile_upload_and_device_tokens
Revises: 003_global_retrieval_domain
Create Date: 2026-05-13 12:15:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "004_mobile_upload_and_device_tokens"
down_revision = "003_global_retrieval_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("device_token_id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("device_token", sa.String(length=512), nullable=False, unique=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"], unique=False)

    op.create_table(
        "upload_sessions",
        sa.Column("upload_id", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.String(length=36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("video_id", sa.String(length=36), sa.ForeignKey("video_resources.video_id"), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("total_size", sa.BigInteger(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("uploaded_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="INIT"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_upload_sessions_owner_id", "upload_sessions", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_upload_sessions_owner_id", table_name="upload_sessions")
    op.drop_table("upload_sessions")
    op.drop_index("ix_device_tokens_user_id", table_name="device_tokens")
    op.drop_table("device_tokens")
