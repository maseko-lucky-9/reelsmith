"""A real 1080p render lands on the capped 1080x1920 canvas (T053).

Before the cap a 1920x1080 source rendered to 1920x3412 (4K: 3840x6826), which
is what made the blur, the caption PNGs and the encode slow.
"""

from __future__ import annotations

from pathlib import Path

import av
import pytest

from app.services import ffmpeg_tools, render_service

pytestmark = pytest.mark.e2e


def _source(path: Path, size: str) -> None:
    ffmpeg_tools.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        timeout=60,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ("1920x1080", (1080, 1920)),
        ("1280x720", (1080, 1920)),
        ("640x360", (640, 1136)),  # under the cap: source width, even-cropped
    ],
)
def test_reel_renders_on_the_capped_canvas(tmp_path, size, expected):
    src, out = tmp_path / "src.mp4", tmp_path / "reel.mp4"
    _source(src, size)

    render_service.render_clip(str(src), str(out), 0.0, 2.0, word_timings=[])

    with av.open(str(out)) as container:
        stream = container.streams.video[0]
        assert (stream.width, stream.height) == expected
        assert stream.codec_context.name == "h264"
        assert stream.codec_context.pix_fmt == "yuv420p"
    assert ffmpeg_tools.video_size(str(out)) == expected
