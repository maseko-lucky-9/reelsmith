"""The integration ``db_store`` fixture TRUNCATEs tables: it must only ever
reach a local test database."""

from __future__ import annotations

import pytest

from tests.db_safety import (
    DEFAULT_TEST_DB_URL,
    UnsafeTestDatabaseError,
    resolve_test_db_url,
)


def test_defaults_to_the_local_docker_compose_database():
    assert resolve_test_db_url({}) == DEFAULT_TEST_DB_URL
    assert "@localhost:5432/" in DEFAULT_TEST_DB_URL


def test_ignores_the_app_database_url():
    """YTVIDEO_DB_URL may point at a real database (e.g. from .env)."""
    environ = {"YTVIDEO_DB_URL": "postgresql+asyncpg://u:p@prod.example.com/reelsmith"}

    assert resolve_test_db_url(environ) == DEFAULT_TEST_DB_URL


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_accepts_a_local_test_database_override(host):
    url = f"postgresql+asyncpg://reelsmith:reelsmith@{host}:55432/reelsmith"

    assert resolve_test_db_url({"YTVIDEO_TEST_DB_URL": url}) == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://u:p@db.example.com:5432/reelsmith",
        "postgresql+asyncpg://u:p@10.0.0.5/reelsmith",
        "postgresql+asyncpg://u:p@localhost.evil.com/reelsmith",
        "sqlite+aiosqlite:///./reelsmith.db",
    ],
)
def test_refuses_a_non_local_test_database(url):
    with pytest.raises(UnsafeTestDatabaseError, match="local"):
        resolve_test_db_url({"YTVIDEO_TEST_DB_URL": url})


def test_db_store_fixture_refuses_before_touching_a_remote_database(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(
        "YTVIDEO_TEST_DB_URL", "postgresql+asyncpg://u:p@db.example.com:5432/x"
    )

    with pytest.raises(UnsafeTestDatabaseError):
        request.getfixturevalue("db_store")
