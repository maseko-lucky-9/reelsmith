"""add ai_hook_text + ai_hook_audio_path to clips (W1.7)

Revision ID: f4g5h6i7j8k9
Revises: e3f4g5h6i7j8
Create Date: 2026-05-10 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4g5h6i7j8k9'
down_revision: Union[str, None] = 'e3f4g5h6i7j8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.add_column(sa.Column("ai_hook_text", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("ai_hook_audio_path", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.drop_column("ai_hook_audio_path")
        batch_op.drop_column("ai_hook_text")
