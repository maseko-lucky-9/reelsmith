"""A real edited-timeline render through the bundled ffmpeg (spec 016 T128).

A generated 320x180 testsrc clip with a 440 Hz tone and a 22.05 kHz mono
880 Hz voice-over wav (a Piper-like file) are rendered from a v2 timeline:
two segments, a caption preset, a text overlay and the voice-over. The
output is probed with PyAV: its length is the sum of the segments, it holds
one video and one audio stream, the voice-over is in the mix where it was
placed and the source is ducked under it.
"""

from __future__ import annotations

import av
import numpy as np
import pytest

from app.services import ffmpeg_tools, render_service
from app.services.timeline_render_service import build_render_plan

pytestmark = pytest.mark.e2e

SEGMENTS = [{"start": 0.5, "end": 1.7}, {"start": 3.0, "end": 4.3}]
TOTAL = 2.5  # 1.2 + 1.3
VO_START, VO_END = 1.0, 2.0


def _lavfi(out, *sources, extra=()):
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    for source in sources:
        argv += ["-f", "lavfi", "-i", source]
    ffmpeg_tools.run([*argv, *extra, str(out)], timeout=60)


def _tone(pcm, rate, lo, hi, freq):
    seg = pcm[int(lo * rate) : int(hi * rate)]
    t = np.arange(len(seg)) / rate
    return abs(np.dot(seg, np.exp(-2j * np.pi * freq * t))) / len(seg) * 2


def test_plan_renders_segments_and_voiceover_in_one_pass(tmp_path):
    src = tmp_path / "src.mp4"
    _lavfi(
        src,
        "testsrc=size=320x180:rate=25:duration=6",
        "sine=frequency=440:sample_rate=44100:duration=6",
        extra=["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
    )
    clip = tmp_path / "clip"
    clip.mkdir()
    _lavfi(clip / "vo.wav", "sine=frequency=880:sample_rate=22050:duration=3", extra=["-ac", "1"])
    timeline = {
        "schema_version": 2,
        "caption_style": "hormozi",
        "tracks": [
            {"kind": "video", "items": SEGMENTS},
            {
                "kind": "caption",
                "items": [
                    {
                        "start": 0.1,
                        "end": 1.0,
                        "text": "hello there",
                        "words": [{"start": 0.1, "end": 0.5}, {"start": 0.5, "end": 1.0}],
                    }
                ],
            },
            {
                "kind": "text-overlay",
                "items": [{"start": 0.0, "end": 2.0, "text": "HOOK: a, b; [c] 'd'", "x": 0.5, "y": 0.1}],
            },
            {"kind": "audio", "items": [{"start": VO_START, "end": VO_END, "src": "vo.wav"}]},
        ],
    }
    plan = build_render_plan(timeline, "main", source_path=str(src), source_range=(0.0, 6.0), clip_folder=clip)
    out = tmp_path / "out.mp4"

    render_service.render_clip(str(src), str(out), 0.5, 4.3, plan=plan, crop_track=[(0.0, 0.0), (3.8, 200.0)])

    with av.open(str(out)) as container:
        assert [s.type for s in container.streams] == ["video", "audio"]
        rate = container.streams.audio[0].rate
        pcm = np.concatenate([f.to_ndarray().astype(np.float64).mean(axis=0) for f in container.decode(audio=0)])
    frame = 1 / 25
    assert ffmpeg_tools.duration(out, "video") == pytest.approx(TOTAL, abs=frame)
    assert ffmpeg_tools.duration(out, "audio") == pytest.approx(TOTAL, abs=frame)
    assert len(pcm) / rate == pytest.approx(TOTAL, abs=frame)

    before = _tone(pcm, rate, 0.2, 0.9, 440)
    during = _tone(pcm, rate, VO_START + 0.2, VO_END - 0.1, 440)
    # the voice-over plays only where it was placed
    assert _tone(pcm, rate, VO_START + 0.2, VO_END - 0.1, 880) > 0.08
    assert _tone(pcm, rate, 0.2, 0.9, 880) < 0.01
    assert _tone(pcm, rate, VO_END + 0.1, TOTAL - 0.05, 880) < 0.01
    # the source is ducked under it and back afterwards
    assert before > 0.08
    assert during == pytest.approx(before * 0.25, rel=0.2)  # about -12 dB
    assert _tone(pcm, rate, VO_END + 0.1, TOTAL - 0.05, 440) == pytest.approx(before, rel=0.2)
