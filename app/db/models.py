"""SQLAlchemy ORM models.

Phase 1 will add JobRecord, ChapterRecord, ClipRecord.
This stub exists so alembic env.py can import it at Phase 0.
"""
from __future__ import annotations

from app.db.base import Base  # noqa: F401 — ensures Base is the same instance

__all__ = ["Base"]
