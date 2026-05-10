"""W3.1 collab/integrations bundle: workspaces + members + scheduled_posts +
analytics + share_links + webhooks + api_tokens.

Revision ID: l0m1n2o3p4q5
Revises: h6i7j8k9l0m1
Create Date: 2026-05-10 02:00:00.000000

All tables additive. No FK on existing tables yet — that lands as a
follow-up nullable-workspace_id bridge migration so existing rows
can be backfilled to ``'local'`` without a downtime window.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l0m1n2o3p4q5'
down_revision: Union[str, None] = 'h6i7j8k9l0m1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Workspaces
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.bulk_insert(
        sa.table("workspaces", sa.column("id"), sa.column("name")),
        [{"id": "local", "name": "Local"}],
    )

    # Workspace members
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("workspace_id", sa.String(length=36),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE",
                                name="fk_wm_workspace"),
                  nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),  # owner|editor|viewer
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_wm_workspace_user"),
    )
    op.create_index("ix_wm_workspace", "workspace_members", ["workspace_id"])

    # Scheduled posts (W3.2 worker reads this; supersedes W1 APScheduler).
    op.create_table(
        "scheduled_posts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("publish_job_id", sa.String(length=36),
                  sa.ForeignKey("publish_jobs.id", ondelete="CASCADE",
                                name="fk_sp_publish"),
                  nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="scheduled"),  # scheduled|posting|published|failed
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_sp_status_scheduled_for",
                    "scheduled_posts", ["status", "scheduled_for"])

    # Per-clip analytics snapshots (W3.3).
    op.create_table(
        "clip_analytics_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("clip_id", sa.String(length=36),
                  sa.ForeignKey("clips.id", ondelete="CASCADE",
                                name="fk_cas_clip"),
                  nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("external_post_id", sa.String(length=255), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("watch_time_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shares", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_cas_clip", "clip_analytics_snapshots", ["clip_id"])

    # Share links (W3.4).
    op.create_table(
        "share_links",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("clip_id", sa.String(length=36),
                  sa.ForeignKey("clips.id", ondelete="CASCADE",
                                name="fk_sl_clip"),
                  nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_sl_clip", "share_links", ["clip_id"])

    # Outbound webhooks (W3.5).
    op.create_table(
        "webhooks",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),  # ["clip.published", ...]
        sa.Column("secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # API tokens (W3.6).
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("workspace_id", sa.String(length=36),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE",
                                name="fk_at_workspace"),
                  nullable=False, server_default="local"),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("api_tokens")
    op.drop_table("webhooks")
    op.drop_index("ix_sl_clip", table_name="share_links")
    op.drop_table("share_links")
    op.drop_index("ix_cas_clip", table_name="clip_analytics_snapshots")
    op.drop_table("clip_analytics_snapshots")
    op.drop_index("ix_sp_status_scheduled_for", table_name="scheduled_posts")
    op.drop_table("scheduled_posts")
    op.drop_index("ix_wm_workspace", table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
