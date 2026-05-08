"""add jobs.source column

Revision ID: a1b2c3d4e5f6
Revises: 3875333f2905
Create Date: 2026-05-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '3875333f2905'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('source', sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'source')
