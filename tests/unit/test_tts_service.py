"""Unit tests for tts_service (generate:// mode, Stage 1).

Covers the stub provider (deterministic WAV sized to the script length), the
empty-text guard, and the injectable voicebox provider (mock invoker; non-200
or raising invoker surfaces as TtsError).
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.services import tts_service as svc


# ── stub provider ─────────────────────────────────────────────────────────────


def _wav_duration_seconds(path: str) -> float:
    """Read a WAV with the stdlib `wave` module → duration in seconds."""
    with wave.open(path, "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def test_stub_synth_writes_parseable_wav_with_expected_duration(tmp_path):
    out = tmp_path / "vo.wav"
    text = "Stay focused while you work on the thing that matters most today."
    result = svc.synthesize(text, str(out), provider="stub")

    assert result == str(out)
    assert Path(out).is_file()

    expected = max(2.0, len(text) / 15.0)
    actual = _wav_duration_seconds(str(out))
    # Stub samples = int(expected * 24000); rounding loss is < 1 sample-period.
    assert actual == pytest.approx(expected, abs=0.01)


def test_stub_short_text_floors_at_two_seconds(tmp_path):
    out = tmp_path / "vo.wav"
    svc.synthesize("hi", str(out), provider="stub")  # len 2 → 2/15 < 2.0
    assert _wav_duration_seconds(str(out)) == pytest.approx(2.0, abs=0.01)


def test_empty_text_raises(tmp_path):
    with pytest.raises(svc.TtsError):
        svc.synthesize("   ", str(tmp_path / "vo.wav"), provider="stub")


def test_unknown_provider_raises(tmp_path):
    with pytest.raises(svc.TtsError):
        svc.synthesize("hello", str(tmp_path / "vo.wav"), provider="bogus")


# ── voicebox provider (injectable invoker) ────────────────────────────────────


def test_voicebox_invokes_with_endpoint_and_payload(tmp_path):
    out = tmp_path / "vb.wav"
    captured: dict = {}
    wav_bytes = svc._wav_header(24000, 24000) + b"\x00\x00" * 24000

    def fake_invoker(endpoint: str, api_key, payload: dict) -> bytes:
        captured["endpoint"] = endpoint
        captured["api_key"] = api_key
        captured["payload"] = payload
        return wav_bytes

    result = svc.synthesize(
        "narration text",
        str(out),
        provider="voicebox",
        endpoint="https://voicebox.example/api/tts",
        api_key="secret-token",
        voice_profile="warm-narrator",
        invoker=fake_invoker,
    )

    assert result == str(out)
    assert Path(out).read_bytes() == wav_bytes
    assert captured["endpoint"] == "https://voicebox.example/api/tts"
    assert captured["api_key"] == "secret-token"
    assert captured["payload"] == {
        "text": "narration text",
        "voice_profile": "warm-narrator",
    }


def test_voicebox_missing_endpoint_raises(tmp_path):
    with pytest.raises(svc.TtsError):
        svc.synthesize(
            "x", str(tmp_path / "vb.wav"), provider="voicebox", invoker=lambda *a: b""
        )


def test_voicebox_raising_invoker_surfaces_as_tts_error(tmp_path):
    def boom(endpoint, api_key, payload):
        raise svc.TtsError("voicebox request failed (status=500): server error")

    with pytest.raises(svc.TtsError):
        svc.synthesize(
            "x",
            str(tmp_path / "vb.wav"),
            provider="voicebox",
            endpoint="https://voicebox.example/api/tts",
            invoker=boom,
        )


def test_voicebox_empty_response_raises(tmp_path):
    with pytest.raises(svc.TtsError):
        svc.synthesize(
            "x",
            str(tmp_path / "vb.wav"),
            provider="voicebox",
            endpoint="https://voicebox.example/api/tts",
            invoker=lambda *a: b"",
        )
