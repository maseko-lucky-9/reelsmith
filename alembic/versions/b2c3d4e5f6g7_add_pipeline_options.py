"""add pipeline_options and job scalars

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use batch_alter_table for SQLite-dev compatibility (G21)
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(
            sa.Column("segment_mode", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("language", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("prompt", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("auto_hook", sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("brand_template_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("pipeline_options", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("pipeline_options")
        batch_op.drop_column("brand_template_id")
        batch_op.drop_column("auto_hook")
        batch_op.drop_column("prompt")
        batch_op.drop_column("language")
        batch_op.drop_column("segment_mode")
