"""The SQLite -> Postgres copy against a real Postgres (``reelsmith_test``).

Run with: pytest -m integration (needs ``docker compose up -d postgres``).

asyncpg reads a naive datetime as the machine's local time, so the copy only
keeps timestamps exact because it stamps them UTC. This runs in a UTC+2 zone
to make that visible; a UTC machine would hide the bug.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import upgrade_to_head
from scripts import migrate_sqlite_to_postgres as mig
from tests.db_safety import resolve_test_db_url

pytestmark = pytest.mark.integration

CREATED = "2026-05-02 18:22:08.453443"


@pytest.fixture
def johannesburg_clock():
    # Not monkeypatch: its undo() would also revert db_store's patches early.
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Africa/Johannesburg"
    time.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "reelsmith.db"
    upgrade_to_head(f"sqlite+aiosqlite:///{path}")
    with sqlite3.connect(path) as conn:
        conn.executescript(
            f"""
            INSERT INTO jobs (id, youtube_url, status, pipeline_options, chapters_total, chapters_done,
                              created_at, updated_at)
              VALUES ('job-tz', 'https://yt.test/tz', 'completed', '{{"render": true}}', 1, 1,
                      '{CREATED}', '{CREATED}');
            INSERT INTO chapters (id, job_id, chapter_index, title, start_time, end_time)
              VALUES ('ch-tz', 'job-tz', 0, '', 0, 0);
            INSERT INTO clips (id, job_id, chapter_id, start, end, hashtags, liked, disliked, retired,
                               caption_style, created_at, updated_at)
              VALUES ('clip-tz', 'job-tz', 'ch-tz', 1, 2, '["#a"]', 1, 0, 0, 'static', '{CREATED}', '{CREATED}');
            """
        )
    return path


async def test_every_row_and_timestamp_arrives_exactly(db_store, source, tmp_path, johannesburg_clock):
    url = resolve_test_db_url(os.environ)

    report = await mig.run(source, url, backup_dir=tmp_path / "backups")

    assert report.missing == {}
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            job = (await conn.execute(text("SELECT created_at, pipeline_options FROM jobs WHERE id='job-tz'"))).one()
            clip = (await conn.execute(text("SELECT chapter_id, hashtags, liked FROM clips WHERE id='clip-tz'"))).one()
    finally:
        await engine.dispose()
    assert job.created_at == datetime(2026, 5, 2, 18, 22, 8, 453443, tzinfo=UTC)
    assert job.pipeline_options == {"render": True}
    assert (clip.chapter_id, clip.hashtags, clip.liked) == ("ch-tz", ["#a"], True)
