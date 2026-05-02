"""Contract test isolation: override job store to in-memory so tests never
touch the dev SQLite file and are fully independent of prior runs."""
from __future__ import annotations

import pytest

from app.settings import settings


@pytest.fixture(autouse=True)
def _use_memory_store(monkeypatch):
    monkeypatch.setattr(settings, "job_store", "memory")
    yield
