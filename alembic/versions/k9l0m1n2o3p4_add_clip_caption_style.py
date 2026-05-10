"""add caption_style + caption_styles table (W2.1)

Revision ID: k9l0m1n2o3p4
Revises: g5h6i7j8k9l0
Create Date: 2026-05-10 00:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'k9l0m1n2o3p4'
down_revision: Union[str, None] = 'g5h6i7j8k9l0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TEMPLATES = (
    "static",
    "hormozi",
    "mrbeast",
    "karaoke",
    "boldpop",
    "subtle",
)


def upgrade() -> None:
    # Add per-clip caption-style column.
    with op.batch_alter_table("clips") as batch_op:
        batch_op.add_column(
            sa.Column(
                "caption_style",
                sa.String(length=32),
                nullable=False,
                server_default="static",
            )
        )

    # Catalog of caption-style presets. Seeded with the five Wave 2
    # animated templates plus the default 'static' fall-through.
    op.create_table(
        "caption_styles",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False, unique=True),
        sa.Column("animation_kind", sa.String(length=32), nullable=False),
        sa.Column("font_family", sa.String(length=128), nullable=True),
        sa.Column("font_size", sa.Integer(), nullable=False, server_default="42"),
        sa.Column("primary_color", sa.String(length=16), nullable=False, server_default="#ffffff"),
        sa.Column("highlight_color", sa.String(length=16), nullable=True),
        sa.Column("stroke_color", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    seed = sa.table(
        "caption_styles",
        sa.column("id"),
        sa.column("name"),
        sa.column("animation_kind"),
        sa.column("primary_color"),
        sa.column("highlight_color"),
        sa.column("stroke_color"),
    )
    rows = [
        {"id": f"caption-style-{name}", "name": name, "animation_kind": name,
         "primary_color": "#ffffff",
         "highlight_color": (
             "#fff200" if name == "hormozi" else
             "#ff0066" if name == "mrbeast" else
             "#7c3aed" if name == "boldpop" else
             "#ffffff"),
         "stroke_color": "#000000"}
        for name in _TEMPLATES
    ]
    op.bulk_insert(seed, rows)


def downgrade() -> None:
    op.drop_table("caption_styles")
    with op.batch_alter_table("clips") as batch_op:
        batch_op.drop_column("caption_style")
