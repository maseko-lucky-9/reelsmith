"""clip_service: chapter audio extraction, safe chapter end and background still.

The MoviePy helpers (``create_clip``, ``extract_chapter_to_disk``, …) were
removed in P1; the reel is rendered straight from the source by
``render_service``. The caption schedule and canvas geometry are pinned in
``test_caption_entries_characterization.py``.
"""

from pathlib import Path

import av
import numpy as np
import pytest
from PIL import Image

from app.services.clip_service import (
    AUDIO_TAIL_EPSILON_SECONDS,
    create_background,
    extract_audio,
    extract_audio_argv,
    probe_safe_end,
)
from tests.fixtures.make_sync_fixture import SyncFixture
from tests.sync_checker import click_onsets

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample.mp4"


# ── probe_safe_end ────────────────────────────────────────────────────────────


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture sample.mp4 missing")
def test_probe_safe_end_subtracts_epsilon():
    safe = probe_safe_end(str(FIXTURE))
    # sample.mp4 is 5.0s with audio; safe_end == 5.0 - 1.0 = 4.0
    assert safe == pytest.approx(5.0 - AUDIO_TAIL_EPSILON_SECONDS, abs=0.05)


# ── extract_audio ─────────────────────────────────────────────────────────────


def test_extract_audio_argv_uses_the_render_window():
    argv = extract_audio_argv("/src.mp4", 1.25, 3.5, "/out.wav")
    assert argv[0] == "ffmpeg"
    i = argv.index("-i")
    assert argv[i - 4 : i + 2] == [
        "-ss",
        "1.250000",
        "-t",
        "3.500000",
        "-i",
        "/src.mp4",
    ]
    for flag, value in (
        ("-map", "0:a:0"),
        ("-ac", "1"),
        ("-ar", "16000"),
        ("-c:a", "pcm_s16le"),
    ):
        assert argv[argv.index(flag) + 1] == value
    assert argv[-1] == "/out.wav"


def test_extract_audio_is_16k_mono_pcm_and_sample_aligned(
    sync_fixture_640: SyncFixture, tmp_path
):
    start, duration = 1.01, 3.0
    wav = tmp_path / "nested" / "chapter.wav"
    assert extract_audio(str(sync_fixture_640.path), start, duration, str(wav)) == str(
        wav
    )
    with av.open(str(wav)) as container:
        stream = container.streams.audio[0]
        ctx = stream.codec_context
        assert (ctx.name, ctx.sample_rate, ctx.channels) == ("pcm_s16le", 16_000, 1)
        samples = sum(f.samples for f in container.decode(stream))
    assert samples == round(duration * 16_000)
    # Clicks at source frames 37, 61, 90 land at (k / fps - start) in the wav,
    # within one 16 kHz sample: sample-accurate against the render's window.
    expected = [
        sync_fixture_640.frame_time(k) - start
        for k in sync_fixture_640.click_frames
        if start <= sync_fixture_640.frame_time(k) < start + duration
    ]
    onsets = click_onsets(wav)
    assert len(onsets) == len(expected) == 3
    for got, want in zip(onsets, expected):
        assert abs(got - want) <= 1 / 16_000, (got, want)


def test_extract_audio_without_audio_stream_returns_none(
    sync_fixture_720: SyncFixture, tmp_path
):
    wav = tmp_path / "chapter.wav"
    assert extract_audio(str(sync_fixture_720.path), 0.5, 1.0, str(wav)) is None
    assert not wav.exists()


@pytest.mark.parametrize(("start", "duration"), [(-0.1, 1.0), (0.0, 0.0), (1.0, -2.0)])
def test_extract_audio_rejects_bad_window(start, duration, tmp_path):
    with pytest.raises(ValueError):
        extract_audio(str(FIXTURE), start, duration, str(tmp_path / "x.wav"))


# ── create_background (PIL logic unchanged from the MoviePy era) ──────────────


@pytest.mark.parametrize(
    ("size", "canvas"),
    [
        ((640, 360), (640, 1137)),
        ((1280, 720), (1280, 2275)),
        ((720, 1280), (720, 1280)),
    ],
)
def test_create_background_canvas_size(size, canvas):
    frame = Image.new("RGB", size, (200, 30, 30))
    bg = create_background(frame, 9 / 16)
    assert bg.size == canvas
    # a flat frame stays flat through the blur
    arr = np.asarray(bg)
    assert np.abs(arr.astype(int) - (200, 30, 30)).max() <= 1
