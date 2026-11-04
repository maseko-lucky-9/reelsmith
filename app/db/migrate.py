"""Run Alembic migrations against an explicit database URL."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

_SCRIPT_LOCATION = Path(__file__).resolve().parents[2] / "alembic"


def upgrade_to_head(db_url: str) -> None:
    """Upgrade the database at ``db_url`` to the latest revision.

    Blocking, and ``alembic/env.py`` runs its own event loop: from async code
    call it through ``asyncio.to_thread``. No ini file is used, so alembic does
    not reset the process-wide logging configuration.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    cfg.attributes["db_url"] = db_url
    command.upgrade(cfg, "head")
