"""A voice-over mixed with the source keeps one stereo 48 kHz track.

``amix`` takes its output format from the first input it sees; with a 22.05 kHz
mono voice-over (piper) next to 44.1 kHz stereo source audio the reel used to
come out 22.05 kHz mono, and the source audio outside the voice-over window was
louder than in the same render without a voice-over.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from PIL import Image

from app.services import clip_service, ffmpeg_tools
from app.services.render_service import SourceWindow, VoiceOver, build_plan_argv

pytestmark = pytest.mark.e2e

_SECONDS = 4.0
_VO = (1.0, 2.0)  # reel seconds the voice-over plays


def _ffmpeg(*args: str) -> None:
    ffmpeg_tools.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args])


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("audio_mix")
    src, vo, bg = d / "src.mp4", d / "vo.wav", d / "bg.png"
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=s=320x180:r=24:d={_SECONDS}",
        "-f", "lavfi", "-i", f"sine=f=440:r=44100:d={_SECONDS},aformat=channel_layouts=stereo",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src),
    )  # fmt: skip
    _ffmpeg("-f", "lavfi", "-i", "sine=f=880:r=22050:d=1.0", "-ac", "1", str(vo))
    _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono:d=1.0", str(d / "quiet.wav"))
    geometry = clip_service.reel_geometry(320, 180)
    Image.new("RGB", geometry.canvas_size, (10, 10, 10)).save(bg)
    return {"src": src, "vo": vo, "quiet": d / "quiet.wav", "bg": bg}


def _render(media, out: Path, *, with_vo: bool, vo: str = "vo") -> Path:
    vos = (VoiceOver(str(media[vo]), _VO[0], _VO[1]),) if with_vo else ()
    argv = build_plan_argv(
        str(media["src"]), str(out),
        segments=(SourceWindow(0.0, _SECONDS),), fps=Fraction(24),
        geometry=clip_service.reel_geometry(320, 180), background_png=str(media["bg"]),
        captions=None, captions_dir=None, source_has_audio=True, voiceovers=vos,
    )  # fmt: skip
    ffmpeg_tools.run(argv)
    return out


def _decode(path: Path) -> tuple[av.audio.stream.AudioStream, np.ndarray]:
    with av.open(str(path)) as c:
        assert len(c.streams.audio) == 1
        s = c.streams.audio[0]
        info = (s.codec_context.sample_rate, s.codec_context.channels)
        data = np.concatenate([f.to_ndarray().astype(np.float64) for f in c.decode(audio=0)], axis=1)
    return info, data


def _db(x: np.ndarray) -> float:
    return 20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-12)


def test_voiceover_mix_is_one_stereo_48k_track(media, tmp_path):
    out = _render(media, tmp_path / "vo.mp4", with_vo=True)
    (rate, channels), _ = _decode(out)
    assert (rate, channels) == (48000, 2)


def _level(path: Path, t0: float, t1: float) -> float:
    (rate, _), data = _decode(path)
    return _db(data[:, int(t0 * rate) : int(t1 * rate)])


def test_source_level_outside_the_voiceover_window_is_unchanged(media, tmp_path):
    plain = _render(media, tmp_path / "plain.mp4", with_vo=False)
    mixed = _render(media, tmp_path / "mixed.mp4", with_vo=True)
    # Well after the voice-over ends.
    assert abs(_level(mixed, 3.0, 3.9) - _level(plain, 3.0, 3.9)) < 0.5


def test_source_is_ducked_about_12_db_under_the_voiceover(media, tmp_path):
    plain = _render(media, tmp_path / "plain.mp4", with_vo=False)
    ducked = _render(media, tmp_path / "ducked.mp4", with_vo=True, vo="quiet")
    drop = _level(plain, 1.2, 1.8) - _level(ducked, 1.2, 1.8)
    assert drop == pytest.approx(12.0, abs=1.0)
