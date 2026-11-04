"""add jobs.chapters_total and jobs.chapters_done

Job progress (T052): how many chapters the job detected and how many have
finished every stage, so the job page can show "3 of 10 chapters done".
Existing rows get 0 for both.

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
Create Date: 2026-10-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "p4q5r6s7t8u9"
down_revision: Union[str, None] = "o3p4q5r6s7t8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "chapters_total", sa.Integer(), nullable=False, server_default=sa.text("0")
            )
        )
        batch_op.add_column(
            sa.Column(
                "chapters_done", sa.Integer(), nullable=False, server_default=sa.text("0")
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("chapters_done")
        batch_op.drop_column("chapters_total")
