"""Unit tests for W2.7 brand_vocabulary_service + W2.8 BrandTemplateFont model."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import BrandTemplate, BrandTemplateFont
from app.services.brand_vocabulary_service import apply_vocabulary


# ── W2.7 vocabulary replacement ────────────────────────────────────────────


def test_vocabulary_passthrough_on_empty():
    assert apply_vocabulary("hello world", None) == "hello world"
    assert apply_vocabulary("", {"a": "b"}) == ""


def test_vocabulary_simple_replace():
    out = apply_vocabulary(
        "OpusClip is great",
        {"OpusClip": "ReelSmith"},
    )
    assert out == "ReelSmith is great"


def test_vocabulary_case_insensitive_match():
    out = apply_vocabulary("opusclip is great", {"OpusClip": "ReelSmith"})
    assert out == "reelsmith is great"


def test_vocabulary_case_preserving_replace_uppercase():
    out = apply_vocabulary("AI rules", {"ai": "intelligence"})
    assert out == "INTELLIGENCE rules"


def test_vocabulary_case_preserving_titlecase():
    out = apply_vocabulary("Ai rules", {"ai": "intelligence"})
    assert out == "Intelligence rules"


def test_vocabulary_word_boundary():
    # 'ai' should not match inside 'rain'.
    out = apply_vocabulary("rainy day", {"ai": "X"})
    assert out == "rainy day"


def test_vocabulary_longer_match_wins():
    out = apply_vocabulary(
        "ai chat is here",
        {"ai": "AI", "ai chat": "Conversation"},
    )
    # Longer key wins.
    assert out.lower().startswith("conversation")


# ── W2.8 BrandTemplateFont model ───────────────────────────────────────────


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_brand_template_can_carry_two_fonts(factory):
    async with factory() as session:
        bt = BrandTemplate(name="Bold")
        session.add(bt)
        await session.flush()
        session.add_all([
            BrandTemplateFont(brand_template_id=bt.id, role="heading",
                              family="Anton", path="/fonts/anton.ttf"),
            BrandTemplateFont(brand_template_id=bt.id, role="body",
                              family="Inter", path="/fonts/inter.ttf"),
        ])
        await session.commit()

    async with factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(
            select(BrandTemplateFont).where(BrandTemplateFont.brand_template_id == bt.id)
        )).scalars().all()
        roles = sorted(r.role for r in rows)
        assert roles == ["body", "heading"]


async def test_font_fk_declared_with_cascade(factory):
    """SQLite ignores FK cascade without PRAGMA foreign_keys=ON, so we
    assert the metadata declaration rather than runtime behaviour. The
    Postgres integration tests exercise the actual cascade."""
    fk = next(iter(BrandTemplateFont.__table__.foreign_keys))
    assert fk.ondelete == "CASCADE"
    assert fk.column.table.name == "brand_templates"
