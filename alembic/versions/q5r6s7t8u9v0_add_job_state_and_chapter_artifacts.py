"""add jobs.state and chapters.artifacts

Durable job history (spec 009): the SQL store kept only the columns of
``jobs``, so a restarted app reloaded jobs without their title, duration,
folders, request options or any chapter artifacts. ``jobs.state`` holds the
JobState fields that have no column of their own; ``chapters.artifacts`` holds
one chapter's ChapterArtifacts. Both are NULL for rows written before this
revision.

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-10-13 09:00:00.000000

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
        batch_op.add_column(sa.Column("state", sa.JSON(), nullable=True))
    with op.batch_alter_table("chapters") as batch_op:
        batch_op.add_column(sa.Column("artifacts", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chapters") as batch_op:
        batch_op.drop_column("artifacts")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("state")
