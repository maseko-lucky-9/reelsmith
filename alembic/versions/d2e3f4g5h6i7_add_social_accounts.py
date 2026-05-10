"""add social_accounts table (W1.3 — OAuth-token-bearing per-platform identities)

Revision ID: d2e3f4g5h6i7
Revises: c1d2e3f4g5h6
Create Date: 2026-05-10 00:10:00.000000

ADR-003 Wave 1. Stores Fernet-encrypted OAuth access/refresh tokens
plus owner_id (default 'local' for single-tenant). Workspace-scoped
in W3 via a nullable workspace_id FK added in a later migration.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2e3f4g5h6i7'
down_revision: Union[str, None] = 'c1d2e3f4g5h6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "social_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("account_handle", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        # Fernet-encrypted blobs (bytes). NEVER stored in plaintext.
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default="local"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.UniqueConstraint(
            "platform", "account_handle", "owner_id",
            name="uq_social_accounts_platform_handle_owner",
        ),
    )
    op.create_index(
        "ix_social_accounts_platform",
        "social_accounts",
        ["platform"],
        unique=False,
    )
    op.create_index(
        "ix_social_accounts_owner_id",
        "social_accounts",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_social_accounts_owner_id", table_name="social_accounts")
    op.drop_index("ix_social_accounts_platform", table_name="social_accounts")
    op.drop_table("social_accounts")
