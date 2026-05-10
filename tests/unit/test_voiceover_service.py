"""Unit tests for voiceover_service (W2.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import voiceover_service as svc


def test_stub_synth_writes_wav(tmp_path):
    out = tmp_path / "vo.wav"
    result = svc.synthesize("hello world", str(out), provider="stub")
    assert result == str(out)
    body = out.read_bytes()
    assert body.startswith(b"RIFF")
    assert b"WAVEfmt " in body
    # 1s of mono 24000Hz silence + 44-byte header
    assert len(body) == 44 + 24000 * 2


def test_empty_text_raises(tmp_path):
    with pytest.raises(svc.VoiceoverError):
        svc.synthesize("   ", str(tmp_path / "vo.wav"), provider="stub")


def test_unknown_provider_raises(tmp_path):
    with pytest.raises(svc.VoiceoverError):
        svc.synthesize("x", str(tmp_path / "vo.wav"), provider="bogus")


def test_coqui_argv_shape():
    argv = svc._coqui_argv(
        "hi", "/tmp/v.wav",
        model="tts_models/multilingual/multi-dataset/xtts_v2",
        voice="speaker_03",
    )
    assert argv[0] == "tts"
    assert "--text" in argv and argv[argv.index("--text") + 1] == "hi"
    assert argv[argv.index("--out_path") + 1] == "/tmp/v.wav"
    assert argv[argv.index("--speaker_idx") + 1] == "speaker_03"


def test_coqui_invokes_subprocess(tmp_path):
    out = tmp_path / "vo.wav"
    captured = []

    def fake(argv):
        captured.append(tuple(argv))
        Path(out).write_bytes(b"fake")

    res = svc.synthesize("hi", str(out), provider="coqui", invoker=fake)
    assert res == str(out)
    assert captured[0][0] == "tts"


def test_coqui_missing_output_raises(tmp_path):
    out = tmp_path / "vo.wav"

    def fake(argv):
        # produce nothing
        pass

    with pytest.raises(svc.VoiceoverError):
        svc.synthesize("hi", str(out), provider="coqui", invoker=fake)


def test_piper_argv(tmp_path):
    out = tmp_path / "vo.wav"
    captured = []

    def fake(argv):
        captured.append(tuple(argv))
        Path(out).write_bytes(b"fake")

    svc.synthesize("hi", str(out), provider="piper", invoker=fake)
    assert captured[0][0] == "piper"
    assert "--text" in captured[0]
