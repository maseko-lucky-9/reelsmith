"""Start-up migrations are skipped by ``YTVIDEO_SKIP_ALEMBIC`` only (T001, E1).

The unprefixed ``SKIP_ALEMBIC`` was renamed without a fallback: setting it
must no longer skip ``alembic upgrade head``.
"""

from __future__ import annotations

from collections.abc import Iterator

import alembic.command
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.bus.job_store import InMemoryJobStore
from app.settings import Settings, settings


@pytest.fixture
def upgrade_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """SQL-mode start-up with ``alembic upgrade`` recorded instead of run."""
    calls: list[str] = []
    monkeypatch.setattr(alembic.command, "upgrade", lambda _cfg, rev: calls.append(rev))
    monkeypatch.setattr(settings, "job_store", "sql")
    monkeypatch.setattr(main_module, "_make_store", InMemoryJobStore)
    monkeypatch.delenv("YTVIDEO_SKIP_ALEMBIC", raising=False)
    monkeypatch.delenv("SKIP_ALEMBIC", raising=False)
    yield calls


def test_skip_alembic_setting_skips_startup_migrations(upgrade_calls, monkeypatch):
    monkeypatch.setattr(settings, "skip_alembic", True)

    with TestClient(main_module.create_app()):
        pass

    assert upgrade_calls == []


def test_old_unprefixed_skip_alembic_no_longer_skips_migrations(
    upgrade_calls, monkeypatch
):
    monkeypatch.setattr(settings, "skip_alembic", False)
    monkeypatch.setenv("SKIP_ALEMBIC", "1")

    with TestClient(main_module.create_app()):
        pass

    assert upgrade_calls == ["head"]


def test_settings_skip_alembic_reads_the_prefixed_env_var(monkeypatch):
    monkeypatch.delenv("SKIP_ALEMBIC", raising=False)
    monkeypatch.setenv("YTVIDEO_SKIP_ALEMBIC", "1")

    assert Settings(_env_file=None).skip_alembic is True


def test_settings_skip_alembic_ignores_the_unprefixed_env_var(monkeypatch):
    monkeypatch.delenv("YTVIDEO_SKIP_ALEMBIC", raising=False)
    monkeypatch.setenv("SKIP_ALEMBIC", "1")

    assert Settings(_env_file=None).skip_alembic is False
