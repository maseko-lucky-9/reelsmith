"""The one-off SQLite -> Postgres copy loses no row and shifts no timestamp.

The source is built the way the real one is: a database at the revision
*before* this feature (``p4q5r6s7t8u9``, no ``jobs.state``), filled with rows
by raw SQL. The target is another SQLite file with foreign keys enforced, so a
wrong table order fails here too; the Postgres run is in
tests/integration/test_migrate_to_postgres.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import app.db.models  # noqa: F401  (registers every mapped table)
from app.db.base import Base
from app.db.migrate import _SCRIPT_LOCATION, upgrade_to_head
from scripts import migrate_sqlite_to_postgres as mig

OLD_REVISION = "p4q5r6s7t8u9"
CREATED = "2026-05-02 18:22:08.453443"


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _migrate(path: Path, revision: str) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    cfg.attributes["db_url"] = _sqlite_url(path)
    command.upgrade(cfg, revision)


@pytest.fixture
def old_source(tmp_path: Path) -> Path:
    """A database at the previous revision holding one of everything that matters."""
    path = tmp_path / "reelsmith.db"
    _migrate(path, OLD_REVISION)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            f"""
            INSERT INTO jobs (id, youtube_url, source, status, pipeline_options, chapters_total,
                              chapters_done, created_at, updated_at)
              VALUES ('job-a', 'https://yt.test/a', 'youtube', 'completed', '{{"render": true}}', 2, 2,
                      '{CREATED}', '{CREATED}'),
                     ('job-b', 'https://yt.test/b', NULL, 'failed', NULL, 0, 0, '{CREATED}', '{CREATED}');
            INSERT INTO chapters (id, job_id, chapter_index, title, start_time, end_time)
              VALUES ('ch-0', 'job-a', 0, '', 0, 0), ('ch-1', 'job-a', 1, '', 0, 0);
            INSERT INTO clips (id, job_id, chapter_id, start, end, output_path, hashtags, transcript,
                               liked, disliked, retired, caption_style, created_at, updated_at)
              VALUES ('clip-1', 'job-a', 'ch-0', 1.5, 9.0, '/clips/1.mp4', '["#a", "#b"]', '{{"text": "hi"}}',
                      1, 0, 0, 'static', '{CREATED}', '{CREATED}'),
                     ('clip-2', 'job-a', NULL, 10, 20, NULL, NULL, NULL, 0, 0, 1, 'hormozi', '{CREATED}', '{CREATED}');
            """
        )
        conn.execute(
            "INSERT INTO social_accounts (id, platform, account_handle, access_token_enc, owner_id, active,"
            " created_at, updated_at) VALUES ('sa-1', 'tiktok', '@me', ?, 'local', 1, ?, ?)",
            (b"\x00\xffsecret", CREATED, CREATED),
        )
    return path


def _target_engine(path: Path) -> AsyncEngine:
    engine = create_async_engine(_sqlite_url(path))

    @event.listens_for(engine.sync_engine, "connect")
    def _enforce_foreign_keys(dbapi_conn, _record) -> None:  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- row transform ----------------------------------------------------------


def test_naive_datetimes_are_stamped_utc_and_everything_else_is_untouched():
    jobs = Base.metadata.tables["jobs"]
    naive = datetime(2026, 5, 2, 18, 22, 8)
    row = {c.key: None for c in jobs.columns} | {
        "id": "x",
        "created_at": naive,
        "updated_at": naive.replace(tzinfo=UTC),
        "chapters_total": 3,
    }

    out = mig._to_target_row(jobs, row)

    assert out["created_at"] == datetime(2026, 5, 2, 18, 22, 8, tzinfo=UTC)
    assert out["created_at"].utcoffset().total_seconds() == 0
    assert out["updated_at"] == datetime(2026, 5, 2, 18, 22, 8, tzinfo=UTC)
    assert (out["id"], out["chapters_total"]) == ("x", 3)


# -- copy_all ---------------------------------------------------------------


async def _copy(source: Path, target: Path) -> tuple[dict[str, int], AsyncEngine]:
    """Upgrade both to head, as ``run`` does, then copy; returns inserted counts."""
    await asyncio.to_thread(upgrade_to_head, _sqlite_url(source))
    await asyncio.to_thread(upgrade_to_head, _sqlite_url(target))
    src, dst = create_async_engine(_sqlite_url(source)), _target_engine(target)
    try:
        return await mig.copy_all(src, dst), dst
    finally:
        await src.dispose()


async def test_every_row_arrives_with_its_values(old_source, tmp_path):
    target = tmp_path / "target.db"

    inserted, dst = await _copy(old_source, target)
    try:
        assert (inserted["jobs"], inserted["chapters"], inserted["clips"]) == (2, 2, 2)
        assert inserted["social_accounts"] == 1
        async with dst.connect() as conn:
            jobs = {r["id"]: r for r in (await conn.execute(select(Base.metadata.tables["jobs"]))).mappings()}
            clips = {r["id"]: r for r in (await conn.execute(select(Base.metadata.tables["clips"]))).mappings()}
            account = (await conn.execute(select(Base.metadata.tables["social_accounts"]))).mappings().one()
        assert jobs["job-a"]["pipeline_options"] == {"render": True}
        assert jobs["job-a"]["state"] is None, "old rows have no stored state"
        assert clips["clip-1"]["hashtags"] == ["#a", "#b"]
        assert clips["clip-1"]["transcript"] == {"text": "hi"}
        assert (clips["clip-1"]["liked"], clips["clip-2"]["retired"]) == (True, True)
        assert clips["clip-1"]["chapter_id"] == "ch-0"
        assert account["access_token_enc"] == b"\x00\xffsecret"
        src = create_async_engine(_sqlite_url(old_source))
        try:
            assert await mig.missing_keys(src, dst) == {}
        finally:
            await src.dispose()
    finally:
        await dst.dispose()


async def test_a_second_copy_adds_nothing(old_source, tmp_path):
    target = tmp_path / "target.db"
    await _copy(old_source, target)
    src, dst = create_async_engine(_sqlite_url(old_source)), _target_engine(target)
    try:
        again = await mig.copy_all(src, dst)
    finally:
        await src.dispose()
        await dst.dispose()

    assert sum(again.values()) == 0


async def test_missing_keys_names_a_row_that_did_not_arrive(old_source, tmp_path):
    target = tmp_path / "target.db"
    _, dst = await _copy(old_source, target)
    src = create_async_engine(_sqlite_url(old_source))
    try:
        async with dst.begin() as conn:
            await conn.exec_driver_sql("DELETE FROM clips WHERE id = 'clip-2'")

        missing = await mig.missing_keys(src, dst)
    finally:
        await src.dispose()
        await dst.dispose()

    assert missing == {"clips": [("clip-2",)]}


async def test_a_failed_insert_does_not_echo_the_row_values(old_source, tmp_path):
    """social_accounts holds encrypted OAuth tokens: SQLAlchemy would print the
    bound parameters of a failing INSERT, which would put them in the console."""
    target = tmp_path / "target.db"
    await asyncio.to_thread(upgrade_to_head, _sqlite_url(old_source))
    await asyncio.to_thread(upgrade_to_head, _sqlite_url(target))
    with sqlite3.connect(target) as conn:
        conn.execute(
            "CREATE TRIGGER refuse BEFORE INSERT ON social_accounts BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
    src, dst = mig._engine(_sqlite_url(old_source)), mig._engine(_sqlite_url(target))
    try:
        with pytest.raises(Exception, match="refused") as caught:
            await mig.copy_all(src, dst)
    finally:
        await src.dispose()
        await dst.dispose()

    assert "secret" not in str(caught.value)
    assert "@me" not in str(caught.value)


# -- run (the whole script) -------------------------------------------------


async def test_run_backs_up_and_leaves_the_original_untouched(old_source, tmp_path):
    before = _digest(old_source)
    target = tmp_path / "target.db"
    backups = tmp_path / "backups"

    report = await mig.run(old_source, _sqlite_url(target), backup_dir=backups)

    assert _digest(old_source) == before, "the source database must never be modified"
    (backup,) = backups.glob("reelsmith-*.db")
    assert backup.exists() and backup != old_source
    assert report.missing == {}
    assert report.inserted["jobs"] == 2


async def test_run_refuses_a_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        await mig.run(tmp_path / "nope.db", _sqlite_url(tmp_path / "t.db"), backup_dir=tmp_path)
