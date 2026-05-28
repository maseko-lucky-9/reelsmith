"""Unit tests for ai_hook_service (W1.7)."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.bus.event_bus import AsyncEventBus
from app.domain.events import EventType
from app.services import ai_hook_service


def _patch_httpx(monkeypatch, *, status: int = 200, response_json: dict | None = None,
                raise_exc: Exception | None = None):
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        if raise_exc:
            raise raise_exc
        return httpx.Response(
            status, json=response_json or {}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(ai_hook_service.httpx, "post", fake_post)
    return captured


def test_generate_hook_happy_path(monkeypatch):
    captured = _patch_httpx(
        monkeypatch,
        response_json={"response": json.dumps({"hook": "You won't believe this trick."})},
    )
    out = ai_hook_service.generate_hook(
        "the transcript", base_url="http://o", model="m", timeout=5
    )
    assert out == "You won't believe this trick."
    assert captured["url"] == "http://o/api/generate"
    body = captured["kwargs"]["json"]
    assert body["model"] == "m"
    assert "the transcript" in body["prompt"]


def test_generate_hook_truncates_long(monkeypatch):
    long_hook = "x" * 200
    _patch_httpx(monkeypatch, response_json={"response": json.dumps({"hook": long_hook})})
    out = ai_hook_service.generate_hook("t", base_url="http://o", model="m", timeout=5,
                                        max_chars=20)
    assert len(out) == 20
    assert out.endswith("…")


def test_generate_hook_empty_input_returns_empty(monkeypatch):
    out = ai_hook_service.generate_hook("   ")
    assert out == ""


def test_generate_hook_swallows_network_error(monkeypatch):
    _patch_httpx(monkeypatch, raise_exc=httpx.ConnectError("boom"))
    out = ai_hook_service.generate_hook("t", base_url="http://o", model="m", timeout=5)
    assert out == ""


def test_generate_hook_swallows_invalid_json(monkeypatch):
    _patch_httpx(monkeypatch, response_json={"response": "not-valid-json"})
    out = ai_hook_service.generate_hook("t", base_url="http://o", model="m", timeout=5)
    assert out == ""


def test_generate_hook_disabled(monkeypatch):
    monkeypatch.setattr(ai_hook_service.settings, "ai_hook_enabled", False)
    out = ai_hook_service.generate_hook("t")
    assert out == ""


# ── Event-bus emit tests ─────────────────────────────────────────────────────


async def test_generate_hook_emits_event_on_success(monkeypatch):
    _patch_httpx(
        monkeypatch,
        response_json={"response": json.dumps({"hook": "Punchy line."})},
    )

    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.AI_HOOK_GENERATED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    out = ai_hook_service.generate_hook(
        "the transcript", base_url="http://o", model="m", timeout=5,
        bus=bus, job_id="job-h",
    )
    await asyncio.wait_for(consumer, timeout=1.0)

    assert out == "Punchy line."
    assert received[0].type is EventType.AI_HOOK_GENERATED
    assert received[0].job_id == "job-h"
    assert received[0].payload["hook"] == "Punchy line."
    assert received[0].payload["model"] == "m"


async def test_generate_hook_no_emit_when_no_hook(monkeypatch):
    _patch_httpx(monkeypatch, response_json={"response": json.dumps({"hook": ""})})

    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.AI_HOOK_GENERATED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    out = ai_hook_service.generate_hook(
        "x", base_url="http://o", model="m", timeout=5,
        bus=bus, job_id="job-h",
    )
    # Give the loop a chance — there should be NO event.
    await asyncio.sleep(0.05)
    assert out == ""
    assert received == []
    consumer.cancel()


def test_generate_hook_no_bus_still_works(monkeypatch):
    """Legacy callers (no bus) keep working — no event emitted."""
    _patch_httpx(
        monkeypatch,
        response_json={"response": json.dumps({"hook": "Hello"})},
    )
    out = ai_hook_service.generate_hook("t", base_url="http://o", model="m", timeout=5)
    assert out == "Hello"
