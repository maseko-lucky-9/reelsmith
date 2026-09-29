"""Unit tests for tts_service (generate:// mode, Stage 2).

Covers the stub provider (deterministic WAV sized to the script length), the
empty-text guard, and the injectable voicebox provider. The ``invoker`` seam
drives the whole Voicebox async REST flow, so tests exercise the branch with
NO live sidecar. A separate test drives the *default* invoker's async flow
(POST /generate → poll /history → download /audio) against a mocked
``httpx.Client``.
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


# ── base-URL normalization (pure) ─────────────────────────────────────────────


def test_normalize_base_strips_trailing_generate_and_slash():
    assert svc._normalize_base("http://127.0.0.1:17493/generate") == "http://127.0.0.1:17493"
    assert svc._normalize_base("http://127.0.0.1:17493/") == "http://127.0.0.1:17493"
    assert svc._normalize_base("http://127.0.0.1:17493") == "http://127.0.0.1:17493"
    assert svc._normalize_base("http://host/generate/") == "http://host"


# ── voicebox provider (injectable invoker) ────────────────────────────────────


def test_voicebox_invokes_with_endpoint_and_profile_engine_payload(tmp_path):
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
        endpoint="http://127.0.0.1:17493/generate",
        api_key="secret-token",
        voice_profile="warm-narrator",
        engine="kokoro",
        invoker=fake_invoker,
    )

    assert result == str(out)
    assert Path(out).read_bytes() == wav_bytes
    assert captured["endpoint"] == "http://127.0.0.1:17493/generate"
    assert captured["api_key"] == "secret-token"
    assert captured["payload"] == {
        "profile_id": "warm-narrator",
        "text": "narration text",
        "engine": "kokoro",
    }


def test_voicebox_missing_endpoint_raises(tmp_path):
    with pytest.raises(svc.TtsError):
        svc.synthesize(
            "x", str(tmp_path / "vb.wav"), provider="voicebox",
            voice_profile="p", invoker=lambda *a: b"",
        )


def test_voicebox_missing_profile_raises(tmp_path):
    """The profile_id is required — an empty voice_profile must raise."""
    with pytest.raises(svc.TtsError) as exc:
        svc.synthesize(
            "x", str(tmp_path / "vb.wav"), provider="voicebox",
            endpoint="http://127.0.0.1:17493",
            voice_profile="",
            invoker=lambda *a: b"WAV",
        )
    assert "profile" in str(exc.value).lower()


def test_voicebox_raising_invoker_surfaces_as_tts_error(tmp_path):
    def boom(endpoint, api_key, payload):
        raise svc.TtsError("voicebox generation failed")

    with pytest.raises(svc.TtsError):
        svc.synthesize(
            "x",
            str(tmp_path / "vb.wav"),
            provider="voicebox",
            endpoint="http://127.0.0.1:17493",
            voice_profile="warm-narrator",
            invoker=boom,
        )


def test_voicebox_empty_response_raises(tmp_path):
    with pytest.raises(svc.TtsError):
        svc.synthesize(
            "x",
            str(tmp_path / "vb.wav"),
            provider="voicebox",
            endpoint="http://127.0.0.1:17493",
            voice_profile="warm-narrator",
            invoker=lambda *a: b"",
        )


# ── default invoker: full async REST flow against a mocked httpx.Client ───────


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b""):
        self.status_code = status_code
        self._json = json_body or {}
        self.content = content
        self.text = ""

    def json(self):
        return self._json


class _FakeClient:
    """Minimal stand-in for httpx.Client.

    ``post`` returns the generation create response; ``get`` is dispatched on
    the path — ``/history/{id}`` returns the queued/poll responses in order,
    ``/audio/{id}`` returns the WAV bytes.
    """

    def __init__(self, *, create, history_seq, audio):
        self._create = create
        self._history_seq = list(history_seq)
        self._audio = audio
        self.calls: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url))
        return self._create

    def get(self, url, headers=None):
        self.calls.append(("GET", url))
        if "/history/" in url:
            return self._history_seq.pop(0)
        if "/audio/" in url:
            return self._audio
        raise AssertionError(f"unexpected GET {url}")


def test_default_invoker_polls_history_then_downloads_audio(tmp_path, monkeypatch):
    """generating → completed → download WAV via /audio/{id}."""
    import httpx as _httpx

    wav = b"RIFFxxxxWAVE-fake-audio"
    create = _FakeResponse(
        200, {"id": "gen-1", "status": "generating", "audio_path": ""}
    )
    history_seq = [
        _FakeResponse(200, {"id": "gen-1", "status": "generating", "audio_path": ""}),
        _FakeResponse(
            200,
            {"id": "gen-1", "status": "completed", "audio_path": "generations/gen-1.wav"},
        ),
    ]
    audio = _FakeResponse(200, content=wav)
    fake = _FakeClient(create=create, history_seq=history_seq, audio=audio)

    monkeypatch.setattr(_httpx, "Client", lambda *a, **kw: fake)
    # No real sleeping between polls.
    monkeypatch.setattr(svc.time, "sleep", lambda *_a, **_k: None)

    out = tmp_path / "vb.wav"
    result = svc.synthesize(
        "hello world",
        str(out),
        provider="voicebox",
        endpoint="http://127.0.0.1:17493/generate",
        voice_profile="warm-narrator",
        engine="kokoro",
    )

    assert result == str(out)
    assert Path(out).read_bytes() == wav
    # Confirm the URLs hit the normalized base.
    assert ("POST", "http://127.0.0.1:17493/generate") in fake.calls
    assert ("GET", "http://127.0.0.1:17493/history/gen-1") in fake.calls
    assert ("GET", "http://127.0.0.1:17493/audio/gen-1") in fake.calls


def test_default_invoker_completed_immediately_skips_polling(tmp_path, monkeypatch):
    """If POST /generate already returns completed, no /history poll is needed."""
    import httpx as _httpx

    wav = b"RIFF-immediate"
    create = _FakeResponse(
        200, {"id": "gen-2", "status": "completed", "audio_path": "generations/gen-2.wav"}
    )
    audio = _FakeResponse(200, content=wav)
    fake = _FakeClient(create=create, history_seq=[], audio=audio)

    monkeypatch.setattr(_httpx, "Client", lambda *a, **kw: fake)
    monkeypatch.setattr(svc.time, "sleep", lambda *_a, **_k: None)

    out = tmp_path / "vb.wav"
    svc.synthesize(
        "hello",
        str(out),
        provider="voicebox",
        endpoint="http://127.0.0.1:17493",
        voice_profile="p",
    )
    assert Path(out).read_bytes() == wav
    # Never polled /history.
    assert not any(c[0] == "GET" and "/history/" in c[1] for c in fake.calls)


def test_default_invoker_reads_local_absolute_audio_path(tmp_path, monkeypatch):
    """When audio_path is an existing absolute file, read it directly (no GET /audio)."""
    import httpx as _httpx

    local_wav = tmp_path / "local.wav"
    local_wav.write_bytes(b"RIFF-local-bytes")

    create = _FakeResponse(
        200, {"id": "gen-3", "status": "completed", "audio_path": str(local_wav)}
    )
    fake = _FakeClient(create=create, history_seq=[], audio=_FakeResponse(500))

    monkeypatch.setattr(_httpx, "Client", lambda *a, **kw: fake)
    monkeypatch.setattr(svc.time, "sleep", lambda *_a, **_k: None)

    out = tmp_path / "vb.wav"
    svc.synthesize(
        "hello",
        str(out),
        provider="voicebox",
        endpoint="http://127.0.0.1:17493",
        voice_profile="p",
    )
    assert Path(out).read_bytes() == b"RIFF-local-bytes"
    assert not any("/audio/" in c[1] for c in fake.calls)


def test_default_invoker_failed_status_raises(tmp_path, monkeypatch):
    import httpx as _httpx

    create = _FakeResponse(
        200, {"id": "gen-4", "status": "generating", "audio_path": ""}
    )
    history_seq = [
        _FakeResponse(200, {"id": "gen-4", "status": "failed", "audio_path": ""}),
    ]
    fake = _FakeClient(create=create, history_seq=history_seq, audio=_FakeResponse(500))

    monkeypatch.setattr(_httpx, "Client", lambda *a, **kw: fake)
    monkeypatch.setattr(svc.time, "sleep", lambda *_a, **_k: None)

    with pytest.raises(svc.TtsError) as exc:
        svc.synthesize(
            "hello",
            str(tmp_path / "vb.wav"),
            provider="voicebox",
            endpoint="http://127.0.0.1:17493",
            voice_profile="p",
        )
    assert "failed" in str(exc.value)


def test_default_invoker_non_200_create_raises(tmp_path, monkeypatch):
    import httpx as _httpx

    create = _FakeResponse(503)
    create.text = "service unavailable"
    fake = _FakeClient(create=create, history_seq=[], audio=_FakeResponse(500))

    monkeypatch.setattr(_httpx, "Client", lambda *a, **kw: fake)

    with pytest.raises(svc.TtsError) as exc:
        svc.synthesize(
            "hello",
            str(tmp_path / "vb.wav"),
            provider="voicebox",
            endpoint="http://127.0.0.1:17493",
            voice_profile="p",
        )
    assert "503" in str(exc.value)
