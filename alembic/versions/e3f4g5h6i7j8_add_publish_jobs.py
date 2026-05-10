"""add publish_jobs table (W1.4 — scheduled / immediate platform posts)

Revision ID: e3f4g5h6i7j8
Revises: d2e3f4g5h6i7
Create Date: 2026-05-10 00:20:00.000000

ADR-003 Wave 1. APScheduler scaffolds the worker; W3 replaces it
with a Postgres SKIP LOCKED poller and reads from the same table.

Status state machine:
    pending  -> queued  -> posting -> published
                       -> failed
                       -> cancelled

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3f4g5h6i7j8'
down_revision: Union[str, None] = 'd2e3f4g5h6i7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publish_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "clip_id",
            sa.String(length=36),
            sa.ForeignKey("clips.id", ondelete="CASCADE", name="fk_publish_jobs_clip_id"),
            nullable=False,
        ),
        sa.Column(
            "social_account_id",
            sa.String(length=36),
            sa.ForeignKey(
                "social_accounts.id",
                ondelete="CASCADE",
                name="fk_publish_jobs_social_account_id",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hashtags", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("schedule_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_post_id", sa.String(length=255), nullable=True),
        sa.Column("external_post_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
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
    )
    op.create_index("ix_publish_jobs_clip_id", "publish_jobs", ["clip_id"])
    op.create_index("ix_publish_jobs_status", "publish_jobs", ["status"])
    op.create_index("ix_publish_jobs_schedule_at", "publish_jobs", ["schedule_at"])


def downgrade() -> None:
    op.drop_index("ix_publish_jobs_schedule_at", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_status", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_clip_id", table_name="publish_jobs")
    op.drop_table("publish_jobs")
