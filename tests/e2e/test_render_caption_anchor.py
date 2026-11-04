"""Captions on a source that is already 9:16 are drawn in full, not clipped."""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest
from PIL import Image

from app.services import caption_track, clip_service, ffmpeg_tools
from app.services.render_service import SourceWindow, build_plan_argv

pytestmark = pytest.mark.e2e


def test_caption_track_rect_is_fully_visible_on_a_9x16_source(tmp_path):
    geometry = clip_service.reel_geometry(576, 1024)
    entries = [clip_service.CaptionEntry("HELLO THERE FRIEND", 1, 0.0, 1.0)]
    track = caption_track.build_caption_track(entries, geometry, 1.0, tmp_path)
    assert track.height >= 100  # a whole line of text, not a 16 px sliver
    assert track.y + track.height <= geometry.canvas_size[1]


def test_rendered_frame_has_caption_pixels_in_the_lower_third(tmp_path):
    src, bg, out = tmp_path / "src.mp4", tmp_path / "bg.png", tmp_path / "out.mp4"
    ffmpeg_tools.run([
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=576x1024:r=24:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
    ])  # fmt: skip
    geometry = clip_service.reel_geometry(576, 1024)
    Image.new("RGB", geometry.canvas_size, (0, 0, 0)).save(bg)
    entries = [clip_service.CaptionEntry("HELLO THERE FRIEND", 1, 0.0, 1.0)]
    track = caption_track.build_caption_track(entries, geometry, 1.0, tmp_path)
    argv = build_plan_argv(
        str(src), str(out), segments=(SourceWindow(0.0, 1.0),), fps=Fraction(24),
        geometry=geometry, background_png=str(bg), captions=track,
        captions_dir=str(tmp_path), source_has_audio=False,
    )  # fmt: skip
    ffmpeg_tools.run(argv)
    frame = np.asarray(ffmpeg_tools.grab_frame(out, 0.3).convert("L"))
    h = frame.shape[0]
    lit = (frame[2 * h // 3 :] > 128).sum()
    assert lit > 500, lit  # white caption text on the black frame
