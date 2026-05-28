"""add clips.captions_burnt_path

Backfills a model column that landed in app/db/models.py without an
accompanying migration. The column tracks the on-disk path of a clip's
final, captions-burnt MP4 (produced by the animated-caption burn-in
service). Nullable since legacy clips don't have one.

Revision ID: m1n2o3p4q5r6
Revises: l0m1n2o3p4q5
Create Date: 2026-05-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "m1n2o3p4q5r6"
down_revision: Union[str, None] = "l0m1n2o3p4q5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.add_column(
            sa.Column("captions_burnt_path", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.drop_column("captions_burnt_path")
