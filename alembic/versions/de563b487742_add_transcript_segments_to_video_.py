"""add_transcript_segments_to_video_resources

Revision ID: de563b487742
Revises: 006_workflow_state_failed
Create Date: 2026-05-22 19:20:38.491227

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'de563b487742'
down_revision: Union[str, None] = '006_workflow_state_failed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('video_resources', sa.Column('transcript_segments', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('video_resources', 'transcript_segments')
