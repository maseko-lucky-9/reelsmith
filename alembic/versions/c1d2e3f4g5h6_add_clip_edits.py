"""add clip_edits table (W1.1 — inline editor timeline state)

Revision ID: c1d2e3f4g5h6
Revises: b2c3d4e5f6g7
Create Date: 2026-05-10 00:00:00.000000

ADR-003 §Wave 1 · One row per clip; ``timeline`` JSON holds the
multi-track editor state (video / caption / text-overlay tracks).
``version`` increments on each PATCH for optimistic concurrency
and audit. Backward-compatible: existing clips without an edit row
continue to render via the main pipeline as today.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4g5h6'
down_revision: Union[str, None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clip_edits",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "clip_id",
            sa.String(length=36),
            sa.ForeignKey("clips.id", ondelete="CASCADE", name="fk_clip_edits_clip_id"),
            nullable=False,
        ),
        sa.Column("timeline", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("clip_id", name="uq_clip_edits_clip_id"),
    )
    op.create_index(
        "ix_clip_edits_clip_id",
        "clip_edits",
        ["clip_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_clip_edits_clip_id", table_name="clip_edits")
    op.drop_table("clip_edits")
