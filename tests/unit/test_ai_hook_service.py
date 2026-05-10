"""Unit tests for ai_hook_service (W1.7)."""
from __future__ import annotations

import json

import httpx
import pytest

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
