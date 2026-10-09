"""Which database the destructive integration fixtures may use.

``db_store`` (tests/conftest.py) TRUNCATEs the job tables, so it never uses
the app's ``YTVIDEO_DB_URL`` (a developer ``.env`` may point that at a real
database). It uses ``YTVIDEO_TEST_DB_URL``, defaulting to the local
docker-compose / CI Postgres, and refuses any host that is not this machine.

Throwaway local Postgres on another port, e.g.::

    YTVIDEO_TEST_DB_URL=postgresql+asyncpg://reelsmith:reelsmith@127.0.0.1:55432/reelsmith \\
        pytest -m integration
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from sqlalchemy.engine import make_url

DEFAULT_TEST_DB_URL: Final = (
    "postgresql+asyncpg://reelsmith:reelsmith@localhost:5432/reelsmith"
)
_LOCAL_HOSTS: Final = frozenset({"localhost", "127.0.0.1", "::1"})


class UnsafeTestDatabaseError(RuntimeError):
    """The configured test database is not on this machine."""


def resolve_test_db_url(environ: Mapping[str, str]) -> str:
    """The test database URL from ``environ``, checked to be local.

    Raises:
        UnsafeTestDatabaseError: the URL's host is not localhost/127.0.0.1/::1.
    """
    url = environ.get("YTVIDEO_TEST_DB_URL") or DEFAULT_TEST_DB_URL
    host = make_url(url).host
    if host not in _LOCAL_HOSTS:
        raise UnsafeTestDatabaseError(
            f"refusing to TRUNCATE a non-local test database (host={host!r}); "
            "YTVIDEO_TEST_DB_URL must point at localhost, 127.0.0.1 or ::1"
        )
    return url
