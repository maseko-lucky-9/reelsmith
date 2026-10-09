"""Migrations and ORM models describe the same schema (T018).

Deployed databases were built by the Alembic migrations, so the migrations
are the source of truth: the models must match them, not the reverse.

No network and no server: the drift test upgrades a fresh temp SQLite file
to ``head`` and requires an empty ``compare_metadata``; the offline-SQL test
only renders the Postgres dialect. Live Postgres parity is checked in CI
(``alembic check`` against the Postgres 16 service).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

import app.db.models  # noqa: F401  (registers every mapped table)
from app.db.base import Base
from app.settings import settings

_SCRIPT_LOCATION = Path(__file__).resolve().parents[2] / "alembic"


def _alembic_config(output_buffer: io.StringIO | None = None) -> Config:
    # No ini file: env.py skips fileConfig(), so the test does not reset the
    # process-wide logging configuration for later tests.
    cfg = Config(output_buffer=output_buffer)
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    return cfg


@pytest.fixture
def migrated_sqlite(tmp_path, monkeypatch) -> str:
    """Path of a fresh SQLite file upgraded to head by the real migrations."""
    db_file = tmp_path / "parity.db"
    monkeypatch.setattr(settings, "db_url", f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(_alembic_config(), "head")
    return str(db_file)


def test_models_match_migrated_schema(migrated_sqlite: str) -> None:
    engine = create_engine(f"sqlite:///{migrated_sqlite}")
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"models drift from migrations: {diff}"


def test_offline_sql_generation_renders_every_revision(monkeypatch) -> None:
    """``alembic upgrade head --sql`` must render, including data migrations.

    Offline mode only renders SQL for the URL's dialect; nothing connects, so
    no Postgres server is needed. Postgres is the dialect that supports it:
    on SQLite, batch ALTER COLUMN (``n2o3p4q5r6s7``) needs a live connection
    to reflect the table, so offline SQLite output is not a supported path.
    """
    monkeypatch.setattr(
        settings, "db_url", "postgresql+asyncpg://offline:offline@localhost:1/offline"
    )
    buf = io.StringIO()
    command.upgrade(_alembic_config(output_buffer=buf), "head", sql=True)
    sql = buf.getvalue()
    assert "INSERT INTO caption_styles" in sql
    assert "INSERT INTO workspaces" in sql
    assert "'caption-style-hormozi'" in sql
    assert "'local'" in sql
