"""add clips.segment_origin

How a clip's segment was produced: ``chapters`` (the source's own chapter
list), ``discovered`` (Reelsmith's segment proposer) or ``full_video`` (the
single "Full Video" pseudo-chapter). The publisher posts only clips cut from
real chapters, so the manifest carries it. NULL for rows written before this
revision: the origin cannot be recomputed from stored data, so it stays
unknown and the manifest writes an empty cell.

Revision ID: r6s7t8u9v0w1
Revises: q5r6s7t8u9v0
Create Date: 2026-11-02 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "r6s7t8u9v0w1"
down_revision: Union[str, None] = "q5r6s7t8u9v0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.add_column(
            sa.Column("segment_origin", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.drop_column("segment_origin")
