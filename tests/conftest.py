"""Shared pytest fixtures."""
from __future__ import annotations

import os

import pytest
import pytest_asyncio


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
async def db_store(monkeypatch):
    """SqlJobStore on the local test Postgres, with its tables TRUNCATEd.

    The database is ``YTVIDEO_TEST_DB_URL`` (default: ``reelsmith_test`` on the
    local docker-compose / CI Postgres), never the app's ``YTVIDEO_DB_URL``,
    must be on this machine and named ``*_test`` — see ``tests/db_safety.py``.
    It is created and migrated to head if missing, then forced onto both the
    env var and the already-loaded settings. Resets the engine per test.
    """
    import asyncio

    from app.db.migrate import upgrade_to_head
    from app.settings import settings
    from tests.db_safety import ensure_test_database, resolve_test_db_url

    url = resolve_test_db_url(os.environ)
    await ensure_test_database(url)
    await asyncio.to_thread(upgrade_to_head, url)
    monkeypatch.setenv("YTVIDEO_DB_URL", url)
    monkeypatch.setattr(settings, "db_url", url)

    # Reset singletons so each test gets a fresh engine on the current event loop.
    import app.db.engine as _eng
    import app.db.session as _ses
    await _eng.dispose_engine()
    _eng._engine = None
    _ses._factory = None

    from sqlalchemy import text

    from app.bus.job_store import SqlJobStore

    store = SqlJobStore()

    # Wipe tables for a clean slate each test.
    async with store._factory() as session:
        await session.execute(text("TRUNCATE clips, chapters, jobs CASCADE"))
        await session.commit()

    yield store

    await _eng.dispose_engine()
    _eng._engine = None
    _ses._factory = None
