"""widen publish_jobs.status to 32 chars

The TikTok cookie-publish flow introduces the "posted_unverified" status
(17 chars), which overflows the original String(16) column. SQLite ignores
VARCHAR length so dev/CI never noticed, but Postgres would truncate/reject.
Widen to 32 to match the precedent set by scheduled_posts / the initial
clips table.

Revision ID: n2o3p4q5r6s7
Revises: m1n2o3p4q5r6
Create Date: 2026-06-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "n2o3p4q5r6s7"
down_revision: Union[str, None] = "m1n2o3p4q5r6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("publish_jobs") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
            existing_server_default="pending",
        )


def downgrade() -> None:
    with op.batch_alter_table("publish_jobs") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
            existing_server_default="pending",
        )
