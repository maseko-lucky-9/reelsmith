"""brand_templates: vocabulary JSON + brand_template_fonts table (W2.7+W2.8)

Revision ID: h6i7j8k9l0m1
Revises: k9l0m1n2o3p4
Create Date: 2026-05-10 01:00:00.000000

W2.7 — vocabulary JSON ({source: replacement, ...}) applied at
caption-burn time only; never on the persisted transcript.
W2.8 — brand_template_fonts: multi-font support per template
(>= 2 per ADR-003 §Wave 2 spec).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'h6i7j8k9l0m1'
down_revision: Union[str, None] = 'k9l0m1n2o3p4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("brand_templates") as batch_op:
        batch_op.add_column(sa.Column("vocabulary", sa.JSON(), nullable=True))

    op.create_table(
        "brand_template_fonts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "brand_template_id",
            sa.String(length=36),
            sa.ForeignKey(
                "brand_templates.id",
                ondelete="CASCADE",
                name="fk_brand_template_fonts_template",
            ),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),  # heading|body|caption
        sa.Column("family", sa.String(length=128), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_brand_template_fonts_template",
        "brand_template_fonts",
        ["brand_template_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_brand_template_fonts_template", table_name="brand_template_fonts"
    )
    op.drop_table("brand_template_fonts")
    with op.batch_alter_table("brand_templates") as batch_op:
        batch_op.drop_column("vocabulary")
