"""add jobs.title and jobs.duration

The orchestrator sets a job's title and duration after the download, but the
SQL store had no columns for them, so they were lost on the next read and the
job page showed "Loading title..." forever. Nullable: jobs created before this
migration keep no title or duration (the UI falls back to the source URL).

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-10-12 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "q5r6s7t8u9v0"
down_revision: Union[str, None] = "p4q5r6s7t8u9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("title", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("duration", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("duration")
        batch_op.drop_column("title")
