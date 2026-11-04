"""Which database the destructive integration fixtures may use.

``db_store`` (tests/conftest.py) TRUNCATEs the job tables, so it never uses
the app's ``YTVIDEO_DB_URL`` (a developer ``.env`` may point that at a real
database). It uses ``YTVIDEO_TEST_DB_URL``, defaulting to the local
docker-compose / CI Postgres, and refuses any host that is not this machine.

Throwaway local Postgres on another port, e.g.::

    YTVIDEO_TEST_DB_URL=postgresql+asyncpg://reelsmith:reelsmith@127.0.0.1:55432/reelsmith_test \\
        pytest -m integration
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from sqlalchemy.engine import make_url

DEFAULT_TEST_DB_URL: Final = (
    "postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith_test"
)
_TEST_DB_SUFFIX: Final = "_test"
_LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})


class UnsafeTestDatabaseError(RuntimeError):
    """The configured test database is not on this machine."""


def resolve_test_db_url(environ: Mapping[str, str]) -> str:
    """The test database URL from ``environ``, checked to be local.

    Raises:
        UnsafeTestDatabaseError: the URL's host is not localhost/127.0.0.1/::1,
            or its database name does not end in ``_test``.
    """
    url = environ.get("YTVIDEO_TEST_DB_URL") or DEFAULT_TEST_DB_URL
    host = make_url(url).host
    if host not in _LOCAL_HOSTS:
        raise UnsafeTestDatabaseError(
            f"refusing to TRUNCATE a non-local test database (host={host!r}); "
            "YTVIDEO_TEST_DB_URL must point at localhost, 127.0.0.1 or ::1"
        )
    database = make_url(url).database or ""
    if not database.endswith(_TEST_DB_SUFFIX):
        raise UnsafeTestDatabaseError(
            f"refusing to TRUNCATE database {database!r}: a test database name "
            f"must end in {_TEST_DB_SUFFIX!r}"
        )
    return url


async def ensure_test_database(url: str) -> None:
    """Create the database named by ``url`` if it does not exist yet.

    ``url`` must already have passed ``resolve_test_db_url``. Connects to the
    maintenance ``postgres`` database on the same server, so a fresh docker
    volume (or CI service container) needs no manual set-up.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    target = make_url(url)
    admin = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            found = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
            )
            if found.scalar() is None:
                quoted = admin.dialect.identifier_preparer.quote(str(target.database))
                await conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        await admin.dispose()
