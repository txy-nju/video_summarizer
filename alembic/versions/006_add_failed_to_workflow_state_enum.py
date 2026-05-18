"""add FAILED to workflow_state enum

Revision ID: 006_workflow_state_failed
Revises: 005_fix_kb_rel_cascade
Create Date: 2026-05-18 18:10:00
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "006_workflow_state_failed"
down_revision = "005_fix_kb_rel_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep migration idempotent across environments where enum type may be missing.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'workflow_state') THEN
                CREATE TYPE workflow_state AS ENUM (
                    'DRAFT_GENERATING',
                    'WAITING_USER_APPROVAL',
                    'FINAL_GENERATING',
                    'COMPLETED',
                    'FAILED'
                );
            ELSE
                ALTER TYPE workflow_state ADD VALUE IF NOT EXISTS 'FAILED';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # PostgreSQL enum value removal is non-trivial and unsafe in-place.
    pass
