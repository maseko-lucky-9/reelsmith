"""Unit tests for voiceover_service (W2.3)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest import mock

import pytest

from app.bus.event_bus import AsyncEventBus
from app.domain.events import EventType
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

    def fake(argv, stdin_text=""):
        captured.append(tuple(argv))
        Path(out).write_bytes(b"fake")

    res = svc.synthesize("hi", str(out), provider="coqui", invoker=fake)
    assert res == str(out)
    assert captured[0][0] == "tts"


def test_coqui_missing_output_raises(tmp_path):
    out = tmp_path / "vo.wav"

    def fake(argv, stdin_text=""):
        # produce nothing
        pass

    with pytest.raises(svc.VoiceoverError):
        svc.synthesize("hi", str(out), provider="coqui", invoker=fake)


# ── Piper provider tests ──────────────────────────────────────────────────────


def test_piper_argv_shape(tmp_path, monkeypatch):
    """piper is invoked with --model / --output_file; text goes via stdin."""
    out = tmp_path / "vo.wav"
    captured_argv: list[tuple[str, ...]] = []
    captured_stdin: list[str] = []

    monkeypatch.setenv("YTVIDEO_PIPER_MODEL", "/models/en_US-lessac-medium.onnx")

    def fake(argv, stdin_text=""):
        captured_argv.append(tuple(argv))
        captured_stdin.append(stdin_text)
        Path(out).write_bytes(b"fake")

    with mock.patch("shutil.which", return_value="/usr/bin/piper"):
        svc.synthesize("hello piper", str(out), provider="piper", invoker=fake)

    assert len(captured_argv) == 1
    argv = captured_argv[0]
    assert argv[0] == "piper"
    assert "--model" in argv
    model_idx = argv.index("--model")
    assert argv[model_idx + 1] == "/models/en_US-lessac-medium.onnx"
    assert "--output_file" in argv
    out_idx = argv.index("--output_file")
    assert argv[out_idx + 1] == str(out)
    # text must NOT appear in argv — it goes on stdin
    assert "--text" not in argv
    assert captured_stdin[0] == "hello piper"


def test_piper_missing_binary_raises(tmp_path, monkeypatch):
    """When piper binary is absent, VoiceoverError with 'piper' in message."""
    monkeypatch.setenv("YTVIDEO_PIPER_MODEL", "/models/en_US-lessac-medium.onnx")

    with mock.patch("shutil.which", return_value=None):
        with pytest.raises(svc.VoiceoverError, match="piper"):
            svc.synthesize("hello", str(tmp_path / "vo.wav"), provider="piper")


def test_piper_missing_model_env_raises(tmp_path, monkeypatch):
    """When YTVIDEO_PIPER_MODEL is unset, VoiceoverError before invocation."""
    monkeypatch.delenv("YTVIDEO_PIPER_MODEL", raising=False)

    with mock.patch("shutil.which", return_value="/usr/bin/piper"):
        with pytest.raises(svc.VoiceoverError, match="YTVIDEO_PIPER_MODEL"):
            svc.synthesize("hello", str(tmp_path / "vo.wav"), provider="piper")


def test_piper_missing_output_raises(tmp_path, monkeypatch):
    """When piper produces no file, VoiceoverError is raised."""
    monkeypatch.setenv("YTVIDEO_PIPER_MODEL", "/models/en_US-lessac-medium.onnx")

    def fake(argv, stdin_text=""):
        # produce nothing
        pass

    with mock.patch("shutil.which", return_value="/usr/bin/piper"):
        with pytest.raises(svc.VoiceoverError):
            svc.synthesize("hello", str(tmp_path / "vo.wav"), provider="piper", invoker=fake)


# ── Event-bus emit tests ─────────────────────────────────────────────────────


async def test_synthesize_stub_emits_voiceover_generated(tmp_path):
    out = tmp_path / "vo.wav"
    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.VOICEOVER_GENERATED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    svc.synthesize(
        "hello world", str(out), provider="stub",
        bus=bus, job_id="job-vo",
    )
    await asyncio.wait_for(consumer, timeout=1.0)

    assert received[0].type is EventType.VOICEOVER_GENERATED
    assert received[0].job_id == "job-vo"
    assert received[0].payload["provider"] == "stub"
    assert received[0].payload["output"] == str(out)
    assert received[0].payload["text_len"] == len("hello world")


async def test_synthesize_coqui_emits(tmp_path):
    out = tmp_path / "vo.wav"

    def fake(argv, stdin_text=""):
        Path(out).write_bytes(b"fake")

    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.VOICEOVER_GENERATED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    svc.synthesize(
        "hi", str(out), provider="coqui", invoker=fake,
        bus=bus, job_id="job-vo2", voice="speaker_1",
    )
    await asyncio.wait_for(consumer, timeout=1.0)
    assert received[0].payload["provider"] == "coqui"
    assert received[0].payload["voice"] == "speaker_1"


def test_synthesize_without_bus_works(tmp_path):
    """Legacy callers keep working."""
    out = tmp_path / "vo.wav"
    result = svc.synthesize("hello", str(out), provider="stub")
    assert result == str(out)
    assert out.exists()
