"""Unit tests for audio_enhance_service (W1.8).

Argv-shape assertions only — no real ffmpeg invocation.
"""
from __future__ import annotations

import asyncio

import pytest

from app.bus.event_bus import AsyncEventBus
from app.domain.events import EventType
from app.services import audio_enhance_service as svc


def test_loudnorm_argv():
    argv = svc.loudnorm_argv("/in.mp4", "/out.mp4")
    assert argv[0] == "ffmpeg"
    assert "-y" in argv
    assert "-i" in argv and argv[argv.index("-i") + 1] == "/in.mp4"
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in " ".join(argv)
    assert argv[-1] == "/out.mp4"
    # Video stream is preserved with -c:v copy.
    assert ("-c:v", "copy") == (argv[argv.index("-c:v")], argv[argv.index("-c:v") + 1])


def test_rnnoise_argv_with_model():
    argv = svc.rnnoise_argv("/in.mp4", "/out.mp4", model_path="/models/rnnoise.rnn")
    af = argv[argv.index("-af") + 1]
    assert af.startswith("arnndn=m=/models/rnnoise.rnn,")
    assert "loudnorm" in af


def test_rnnoise_argv_without_model():
    argv = svc.rnnoise_argv("/in.mp4", "/out.mp4")
    af = argv[argv.index("-af") + 1]
    assert af.startswith("arnndn,")


def test_enhance_passthrough_copies_file(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"data")
    dst = tmp_path / "out.mp4"
    out = svc.enhance(str(src), str(dst), provider="passthrough")
    assert out == str(dst)
    assert dst.read_bytes() == b"data"


def test_enhance_loudnorm_invokes_ffmpeg(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    dst = tmp_path / "out.mp4"
    captured: list[tuple[str, ...]] = []

    def fake(argv):
        captured.append(tuple(argv))

    out = svc.enhance(str(src), str(dst), provider="loudnorm", invoker=fake)
    assert out == str(dst)
    assert len(captured) == 1
    assert captured[0][0] == "ffmpeg"
    assert "loudnorm" in " ".join(captured[0])


def test_enhance_unknown_provider_raises(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    with pytest.raises(svc.AudioEnhanceError):
        svc.enhance(str(src), str(tmp_path / "out.mp4"), provider="bogus",
                    invoker=lambda argv: None)


def test_enhance_missing_input_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        svc.enhance(str(tmp_path / "missing.mp4"), str(tmp_path / "out.mp4"),
                    provider="loudnorm", invoker=lambda argv: None)


# ── Event-bus emit tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enhance_emits_audio_enhanced_when_bus_provided(tmp_path):
    """When bus + job_id are passed, AUDIO_ENHANCED is published."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    dst = tmp_path / "out.mp4"

    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.AUDIO_ENHANCED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)  # let the subscription register

    svc.enhance(
        str(src), str(dst), provider="loudnorm",
        invoker=lambda argv: None, bus=bus, job_id="job-1",
    )
    await asyncio.wait_for(consumer, timeout=1.0)

    assert len(received) == 1
    ev = received[0]
    assert ev.type is EventType.AUDIO_ENHANCED
    assert ev.job_id == "job-1"
    assert ev.payload["provider"] == "loudnorm"
    assert ev.payload["input"] == str(src)
    assert ev.payload["output"] == str(dst)


@pytest.mark.asyncio
async def test_enhance_passthrough_emits_event(tmp_path):
    """Passthrough provider also emits AUDIO_ENHANCED."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"data")
    dst = tmp_path / "out.mp4"

    bus = AsyncEventBus()
    received = []

    async def collect():
        async for ev in bus.subscribe(types=[EventType.AUDIO_ENHANCED]):
            received.append(ev)
            return

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)

    svc.enhance(str(src), str(dst), provider="passthrough",
                bus=bus, job_id="job-pt")
    await asyncio.wait_for(consumer, timeout=1.0)
    assert received[0].payload["provider"] == "passthrough"


def test_enhance_without_bus_still_works(tmp_path):
    """Legacy no-bus callers keep working (no events emitted)."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"data")
    dst = tmp_path / "out.mp4"
    out = svc.enhance(str(src), str(dst), provider="passthrough")
    assert out == str(dst)
    assert dst.read_bytes() == b"data"
