from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        from app.settings import settings
        kwargs: dict = {"echo": False, "pool_pre_ping": True}
        # SQLite (single-file/memory) doesn't accept pool_recycle on the
        # default StaticPool / NullPool that aiosqlite uses.
        if not settings.db_url.startswith("sqlite"):
            kwargs["pool_recycle"] = getattr(settings, "db_pool_recycle_seconds", 1800)
        _engine = create_async_engine(settings.db_url, **kwargs)
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
