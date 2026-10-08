"""Shared pytest fixtures."""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

_DB_URL = "postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith"


@pytest.fixture(scope="session")
def sync_fixture_640():
    """640x360 23.976 fps B-frame source with frame-index block + AAC click track.

    Built once into the gitignored ``tests/fixtures/generated/`` and reused
    until ``make_sync_fixture.py`` changes.
    """
    from tests.fixtures.make_sync_fixture import build_sync_fixture_640

    return build_sync_fixture_640()


@pytest.fixture(scope="session")
def sync_fixture_720():
    """1280x720 23.976 fps B-frame source with frame-index block, no audio."""
    from tests.fixtures.make_sync_fixture import build_sync_fixture_720

    return build_sync_fixture_720()


@pytest_asyncio.fixture
async def db_store():
    """SqlJobStore backed by the test Postgres instance. Resets engine per test."""
    os.environ["YTVIDEO_DB_URL"] = _DB_URL

    # Reset singletons so each test gets a fresh engine on the current event loop.
    import app.db.engine as _eng
    import app.db.session as _ses
    await _eng.dispose_engine()
    _eng._engine = None
    _ses._factory = None

    from app.bus.job_store import SqlJobStore
    from sqlalchemy import text

    store = SqlJobStore()

    # Wipe tables for a clean slate each test.
    async with store._factory() as session:
        await session.execute(text("TRUNCATE clips, chapters, jobs CASCADE"))
        await session.commit()

    yield store

    await _eng.dispose_engine()
    _eng._engine = None
    _ses._factory = None
