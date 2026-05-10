"""add broll_assets to clips (W1.9)

Revision ID: g5h6i7j8k9l0
Revises: f4g5h6i7j8k9
Create Date: 2026-05-10 00:40:00.000000

Stores resolved B-Roll for a clip. Used by render to overlay clips
and by the manifest CSV to satisfy Pexels attribution.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g5h6i7j8k9l0'
down_revision: Union[str, None] = 'f4g5h6i7j8k9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.add_column(sa.Column("broll_assets", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.drop_column("broll_assets")
