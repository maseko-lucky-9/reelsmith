"""API lifespan warm-up of the Whisper model: background, opt-out, non-fatal."""

from __future__ import annotations

import logging
import threading

import httpx
import pytest
from asgi_lifespan import LifespanManager

from app import main
from app.services import transcription_service


@pytest.fixture
def warm_up_calls(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    called = threading.Event()
    monkeypatch.setattr(transcription_service, "warm_up", called.set)
    monkeypatch.setattr(main.settings, "job_store", "memory")
    monkeypatch.setattr(main.settings, "transcription_provider", "whisper")
    monkeypatch.setattr(main.settings, "whisper_warmup", True)
    return called


async def _health(app) -> int:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return (await client.get("/health")).status_code


async def test_lifespan_warms_the_whisper_model(warm_up_calls: threading.Event) -> None:
    app = main.create_app()
    async with LifespanManager(app):
        assert warm_up_calls.wait(timeout=5)


@pytest.mark.parametrize(
    ("setting", "value"),
    [("whisper_warmup", False), ("transcription_provider", "stub")],
)
async def test_lifespan_skips_warm_up_when_disabled(
    warm_up_calls: threading.Event,
    monkeypatch: pytest.MonkeyPatch,
    setting: str,
    value: object,
) -> None:
    monkeypatch.setattr(main.settings, setting, value)
    app = main.create_app()
    async with LifespanManager(app):
        assert await _health(app) == 200
    assert not warm_up_calls.is_set()


async def test_failed_warm_up_is_logged_and_the_api_still_serves(
    warm_up_calls: threading.Event,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> None:
        warm_up_calls.set()
        raise OSError("model download failed")

    monkeypatch.setattr(transcription_service, "warm_up", broken)
    app = main.create_app()
    with caplog.at_level(logging.WARNING, logger="app.main"):
        async with LifespanManager(app):
            assert warm_up_calls.wait(timeout=5)
            assert await _health(app) == 200
            await app.state.whisper_warmup_task

    assert "Whisper warm-up failed" in caplog.text
    assert "model download failed" in caplog.text
