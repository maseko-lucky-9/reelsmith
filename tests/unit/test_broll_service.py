"""Unit tests for B-Roll service."""
from __future__ import annotations

from app.services.broll_service import LocalBRoll, NoBRoll, get_broll_service
from app.settings import settings


def test_no_broll_returns_none():
    svc = NoBRoll()
    assert svc.find_broll("anything") is None


def test_get_broll_service_none_mode(monkeypatch):
    monkeypatch.setattr(settings, "broll_provider", "none")
    assert isinstance(get_broll_service(), NoBRoll)


def test_get_broll_service_local_mode(monkeypatch):
    monkeypatch.setattr(settings, "broll_provider", "local")
    assert isinstance(get_broll_service(), LocalBRoll)


def test_local_broll_no_clips_returns_none(tmp_path, monkeypatch):
    import app.services.broll_service as bs
    monkeypatch.setattr(bs, "_BROLL_DIR", tmp_path)
    svc = LocalBRoll()
    assert svc.find_broll("A beautiful sunset over the mountains") is None


def test_local_broll_finds_matching_clip(tmp_path, monkeypatch):
    import app.services.broll_service as bs
    monkeypatch.setattr(bs, "_BROLL_DIR", tmp_path)
    (tmp_path / "sunset_timelapse.mp4").write_bytes(b"\x00")
    svc = LocalBRoll()
    result = svc.find_broll("The beautiful sunset over the mountains was amazing")
    assert result is not None
    assert "sunset" in result


def test_local_broll_extract_noun_phrases_fallback(monkeypatch):
    svc = LocalBRoll()
    phrases = svc._extract_noun_phrases("John Smith is talking about Machine Learning today")
    assert isinstance(phrases, list)
