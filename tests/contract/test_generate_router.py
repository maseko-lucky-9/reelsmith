"""Contract: POST /generate request validation + feature-flag gating.

These are fast HTTP-level checks (no pipeline execution) covering the security
hardening on the brief schema:
  * missing title / script → 422 (Pydantic required-field validation)
  * generate mode disabled  → 400 with a "generate mode disabled" detail
  * music_url with a non-http scheme → 422 (field validator)
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import settings


def _valid_body(**overrides) -> dict:
    body = {
        "title": "Generated Reel",
        "script": "A short script about staying focused while working.",
        "shots": [{"prompt": "sunrise over a city", "seconds": 1.0}],
        "voice_profile": "",
        "music_url": "",
    }
    body.update(overrides)
    return body


def test_missing_title_returns_422(monkeypatch):
    monkeypatch.setattr(settings, "generate_enabled", True)
    body = _valid_body()
    del body["title"]
    with TestClient(create_app()) as client:
        resp = client.post("/generate", json=body)
    assert resp.status_code == 422


def test_missing_script_returns_422(monkeypatch):
    monkeypatch.setattr(settings, "generate_enabled", True)
    body = _valid_body()
    del body["script"]
    with TestClient(create_app()) as client:
        resp = client.post("/generate", json=body)
    assert resp.status_code == 422


def test_generate_disabled_returns_400(monkeypatch):
    monkeypatch.setattr(settings, "generate_enabled", False)
    with TestClient(create_app()) as client:
        resp = client.post("/generate", json=_valid_body())
    assert resp.status_code == 400
    assert "generate mode disabled" in resp.json()["detail"].lower()


def test_music_url_non_http_scheme_returns_422(monkeypatch):
    monkeypatch.setattr(settings, "generate_enabled", True)
    with TestClient(create_app()) as client:
        resp = client.post(
            "/generate", json=_valid_body(music_url="file:///etc/passwd")
        )
    assert resp.status_code == 422


def test_music_url_javascript_scheme_returns_422(monkeypatch):
    monkeypatch.setattr(settings, "generate_enabled", True)
    with TestClient(create_app()) as client:
        resp = client.post(
            "/generate", json=_valid_body(music_url="javascript:alert(1)")
        )
    assert resp.status_code == 422
