"""The job SSE stream pings at ``settings.sse_keepalive_seconds`` (T015).

sse-starlette writes a ``: ping`` comment frame every ``ping`` seconds so
proxies don't drop idle long-stage connections; ``0`` disables it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sse_starlette.sse import EventSourceResponse

from app.routers import jobs as jobs_router
from app.settings import Settings


class _FoundJobStore:
    async def get(self, job_id: str) -> object:
        return object()


def _request() -> SimpleNamespace:
    state = SimpleNamespace(event_bus=object(), job_store=_FoundJobStore())
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.parametrize("seconds", [37, 0])
async def test_job_event_stream_pings_at_configured_interval(
    monkeypatch: pytest.MonkeyPatch, seconds: int
) -> None:
    monkeypatch.setattr(jobs_router.settings, "sse_keepalive_seconds", seconds)

    response = await jobs_router.stream_job_events("job-1", _request())

    assert isinstance(response, EventSourceResponse)
    assert response.ping_interval == seconds


def test_sse_keepalive_setting_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YTVIDEO_SSE_KEEPALIVE_SECONDS", "42")

    assert Settings(_env_file=None).sse_keepalive_seconds == 42


def test_sse_keepalive_default_is_15_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YTVIDEO_SSE_KEEPALIVE_SECONDS", raising=False)

    assert Settings(_env_file=None).sse_keepalive_seconds == 15
