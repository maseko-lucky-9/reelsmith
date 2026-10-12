"""The reel canvas is capped at 1080 px wide (T053), and downloads prefer H.264 (T054).

A 1080p source used to get a 1920x3413 canvas (4K: 3840x6826). Everything on the
canvas (blurred background, caption PNGs, the encode) scaled with it, so the
cap is the cheapest speed-up in the render. Sources at or under 1080 px wide are
untouched, which keeps the 640 px fixtures and their golden argv byte-identical.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image
from yt_dlp import YoutubeDL

from app.services import clip_service, render_service
from app.services.platforms import _yt_dlp_base


@pytest.mark.parametrize(
    ("source", "canvas", "inset_height", "anchor"),
    [
        ((1920, 1080), (1080, 1920), 608, 1592),  # 16:9 1080p, the common case
        ((3840, 2160), (1080, 1920), 608, 1592),  # 4K lands on the same canvas
        ((1280, 720), (1080, 1920), 608, 1592),
        ((2560, 1080), (1080, 1920), 456, 1554),  # ultrawide
        ((1080, 1080), (1080, 1920), 1080, 1710),  # at the cap: not resized
        ((640, 360), (640, 1137), 360, 942),  # under the cap: unchanged
        ((1921, 1081), (1080, 1920), 608, 1592),  # odd source size
    ],
)
def test_reel_geometry_caps_the_canvas_width(source, canvas, inset_height, anchor):
    geometry = clip_service.reel_geometry(*source)
    assert geometry.canvas_size == canvas
    assert geometry.inset_height == inset_height
    assert geometry.band_anchor_y == anchor
    assert geometry.source_size == source  # crop tracks stay in real source px


@pytest.mark.parametrize(
    ("source", "inset_height"),
    [
        # ffmpeg's scale=W:-2 rounds the height half-up to an even number; the
        # inset's placement must match what the filter actually produces.
        ((2160, 2162), 1082),  # exact 1081 -> 1082
        ((2160, 2158), 1080),  # exact 1079 -> 1080
        ((640, 361), 362),  # under the cap, odd height: scale=640:-2 -> 362
    ],
)
def test_inset_height_matches_ffmpeg_scale_rounding(source, inset_height):
    assert clip_service.reel_geometry(*source).inset_height == inset_height


def test_inset_is_centred_on_the_capped_canvas():
    geometry = clip_service.reel_geometry(1920, 1080)
    assert render_service.inset_y(geometry) == 656  # (1920 - 608) // 2


def test_pan_window_is_unchanged_by_the_cap():
    """The cap keeps the canvas aspect, so the crop window (source px) is the same."""
    window = render_service.pan_crop(clip_service.reel_geometry(1920, 1080))
    assert (window.width, window.height, window.y) == (608, 1080, 0)


def test_pan_chain_scales_to_the_capped_canvas():
    geometry = clip_service.reel_geometry(1920, 1080)
    chain = render_service._pan_chain(geometry, [(0.0, 0.0), (1.0, 100.0)])
    assert chain.endswith("scale=1080:1920")


def test_background_still_covers_the_canvas_from_the_middle_of_the_frame(monkeypatch):
    """The grabbed frame is 1920 px wide, the canvas 1080. The background must be
    the frame's centre, cover-scaled to the canvas. Without the pre-scale it is
    cropped from a 1920 px wide background instead, which shows too little of the
    frame's right side (red 109/129 at columns 540/1000, not 127/161). Scaling to
    an exact 608 rows would trip create_background's aspect branch and leave
    black bands."""
    ramp = np.linspace(0, 255, 1920).astype(np.uint8)  # red grows left to right
    pixels = np.zeros((1080, 1920, 3), np.uint8)
    pixels[..., 0] = ramp
    pixels[..., 1:] = 40
    frame = Image.fromarray(pixels, "RGB")
    monkeypatch.setattr(render_service.ffmpeg_tools, "grab_frame", lambda *_: frame)
    geometry = clip_service.reel_geometry(1920, 1080)

    bg = render_service.background_still("src.mp4", 0.0, 10.0, geometry)

    assert bg.size == geometry.canvas_size == (1080, 1920)
    red = np.asarray(bg.convert("RGB"))[960, :, 0].astype(int)
    assert 85 <= red[80] <= 100
    assert 118 <= red[540] <= 136
    assert 150 <= red[1000] <= 172
    assert np.asarray(bg.convert("RGB")).mean(axis=(1, 2)).min() > 30, "black bands"


# ── T054: prefer H.264 over VP9/AV1 ───────────────────────────────────────────
#
# Measured on a real video (yt-dlp 2026.08.19): the old selector took format 616
# (VP9 1080p, 4.5 Mb/s) and, on other videos, AV1; the new one takes 137 (H.264
# 1080p). libaom decodes 1080p AV1 ~10x slower than H.264.


def _video(format_id: str, vcodec: str, height: int, tbr: int) -> dict:
    return {
        "format_id": format_id,
        "ext": "mp4",
        "vcodec": vcodec,
        "acodec": "none",
        "width": height * 16 // 9,
        "height": height,
        "tbr": tbr,
        "protocol": "https",
        "url": f"https://example.invalid/{format_id}",
    }


_AUDIO = {
    "format_id": "audio",
    "ext": "m4a",
    "vcodec": "none",
    "acodec": "mp4a.40.2",
    "abr": 128,
    "protocol": "https",
    "url": "https://example.invalid/audio",
}


def _pick(formats: list[dict]) -> list[str]:
    selector = YoutubeDL({"quiet": True}).build_format_selector(
        _yt_dlp_base._DEFAULT_FORMAT
    )
    chosen = list(selector({"formats": [*formats, _AUDIO], "incomplete_formats": False}))
    assert len(chosen) == 1
    return [f["format_id"] for f in chosen[0]["requested_formats"]]


def test_download_prefers_h264_over_higher_bitrate_vp9_and_av1():
    formats = [
        _video("avc-1080", "avc1.640028", 1080, 2000),
        _video("av1-1080", "av01.0.08M.08", 1080, 3000),
        _video("vp9-1080", "vp09.00.40.08", 1080, 4500),
    ]
    assert _pick(formats) == ["avc-1080", "audio"]


def test_download_prefers_h264_even_when_av1_has_more_pixels():
    formats = [
        _video("avc-1080", "avc1.640028", 1080, 2000),
        _video("av1-2160", "av01.0.12M.08", 2160, 8000),
    ]
    assert _pick(formats) == ["avc-1080", "audio"]


def test_download_falls_back_to_av1_when_no_h264_exists():
    formats = [_video("av1-2160", "av01.0.12M.08", 2160, 8000)]
    assert _pick(formats) == ["av1-2160", "audio"]
