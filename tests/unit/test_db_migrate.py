"""``upgrade_to_head`` migrates the database it is given, not ``settings.db_url``.

The migration script, the integration fixture and app start-up share it, so it
must work on an explicit URL (the SQLite backup, the Postgres target) while the
app's own setting points somewhere else.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from app.db.migrate import upgrade_to_head
from app.settings import settings


def _tables(db_file: Path) -> set[str]:
    with sqlite3.connect(db_file) as conn:
        rows = conn.execute("select name from sqlite_master where type='table'").fetchall()
    return {name for (name,) in rows}


def test_upgrades_the_given_url_and_leaves_settings_alone(tmp_path, monkeypatch):
    target = tmp_path / "target.db"
    other = tmp_path / "settings.db"
    monkeypatch.setattr(settings, "db_url", f"sqlite+aiosqlite:///{other}")

    upgrade_to_head(f"sqlite+aiosqlite:///{target}")

    assert {"jobs", "chapters", "clips", "alembic_version"} <= _tables(target)
    assert not other.exists(), "the app's configured database must not be touched"
    assert settings.db_url == f"sqlite+aiosqlite:///{other}"


def test_is_idempotent(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'twice.db'}"

    upgrade_to_head(url)
    upgrade_to_head(url)

    assert "jobs" in _tables(tmp_path / "twice.db")


def test_does_not_reconfigure_process_logging(tmp_path):
    """No ini file => alembic's fileConfig() must not reset the root logger."""
    root = logging.getLogger()
    before = (root.level, list(root.handlers))

    upgrade_to_head(f"sqlite+aiosqlite:///{tmp_path / 'log.db'}")

    assert (root.level, list(root.handlers)) == before


@pytest.mark.asyncio
async def test_callable_from_a_thread_inside_a_running_loop(tmp_path):
    """Start-up runs it via asyncio.to_thread; alembic's env.py uses asyncio.run."""
    import asyncio

    url = f"sqlite+aiosqlite:///{tmp_path / 'loop.db'}"

    await asyncio.to_thread(upgrade_to_head, url)

    assert "jobs" in _tables(tmp_path / "loop.db")
