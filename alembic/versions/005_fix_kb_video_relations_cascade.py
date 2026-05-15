"""fix kb_video_relations schema and cascade constraints

Revision ID: 005_fix_kb_rel_cascade
Revises: 004_mobile_upload_device_tokens
Create Date: 2026-05-15 15:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "005_fix_kb_rel_cascade"
down_revision = "004_mobile_upload_device_tokens"
branch_labels = None
depends_on = None


_TABLE_NAME = "kb_video_relations"
_TMP_TABLE_NAME = "kb_video_relations_tmp"


def _has_required_cascade_fks(inspector: sa.Inspector) -> bool:
    fk_rules = {}
    for fk in inspector.get_foreign_keys(_TABLE_NAME):
        cols = tuple(fk.get("constrained_columns") or [])
        options = fk.get("options") or {}
        fk_rules[cols] = str(options.get("ondelete", "")).upper()

    return (
        fk_rules.get(("kbid",)) == "CASCADE"
        and fk_rules.get(("video_id",)) == "CASCADE"
    )


def _needs_rebuild(inspector: sa.Inspector) -> bool:
    columns = [col["name"] for col in inspector.get_columns(_TABLE_NAME)]
    # Legacy schema contains relation_id/added_at and misses PK(kbid, video_id) contract.
    if "relation_id" in columns or "added_at" in columns:
        return True

    expected = {"kbid", "video_id"}
    if set(columns) != expected:
        return True

    return not _has_required_cascade_fks(inspector)


def _rebuild_table() -> None:
    op.create_table(
        _TMP_TABLE_NAME,
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

    # Keep existing relation pairs when upgrading from legacy schema.
    op.execute(
        sa.text(
            """
            INSERT INTO kb_video_relations_tmp (kbid, video_id)
            SELECT DISTINCT kbid, video_id
            FROM kb_video_relations
            WHERE kbid IS NOT NULL AND video_id IS NOT NULL
            """
        )
    )

    op.drop_table(_TABLE_NAME)
    op.rename_table(_TMP_TABLE_NAME, _TABLE_NAME)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _TABLE_NAME not in inspector.get_table_names():
        _rebuild_table()
        return

    if _needs_rebuild(inspector):
        _rebuild_table()


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _TABLE_NAME not in inspector.get_table_names():
        return

    op.create_table(
        _TMP_TABLE_NAME,
        sa.Column("relation_id", sa.String(length=36), primary_key=True),
        sa.Column("kbid", sa.String(length=36), sa.ForeignKey("knowledge_bases.kbid"), nullable=False),
        sa.Column("video_id", sa.String(length=36), sa.ForeignKey("video_resources.video_id"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("kbid", "video_id", name="uq_kb_video"),
    )

    # Generate deterministic pseudo IDs for downgraded legacy shape.
    op.execute(
        sa.text(
            """
            INSERT INTO kb_video_relations_tmp (relation_id, kbid, video_id, added_at)
            SELECT substr(md5(kbid || ':' || video_id), 1, 36), kbid, video_id, now()
            FROM kb_video_relations
            """
        )
    )

    op.drop_table(_TABLE_NAME)
    op.rename_table(_TMP_TABLE_NAME, _TABLE_NAME)
