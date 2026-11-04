"""add clips.rendered_segments

The source cut a clip's file was last rendered from, ``[[start, end], ...]``
in source seconds, so the editor knows which cut its preview plays. NULL for
rows written before this revision and for clips never edited: the cut is then
the clip's own ``start``/``end`` window.

Revision ID: s7t8u9v0w1x2
Revises: r6s7t8u9v0w1
Create Date: 2026-11-04 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "s7t8u9v0w1x2"
down_revision: Union[str, None] = "r6s7t8u9v0w1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.add_column(sa.Column("rendered_segments", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.drop_column("rendered_segments")
