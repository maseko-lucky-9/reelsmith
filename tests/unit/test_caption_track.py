"""Caption overlay track for the one-pass ffmpeg render (P1).

Covers the pure pieces of ``app.services.caption_track``:

* the shared crop rectangle (union of alpha bounding boxes, even-aligned);
* the caption timeline (gaps are the blank, later captions win overlaps,
  everything clipped to the chapter);
* the ffconcat writer (relative names, ``option framerate 1000``, trailing
  repeated blank, list directories whose path contains a quote);
* ``build_caption_track`` end to end: cropped PNGs are pixel-equal to the
  matching window of ``create_subtitle_image`` (the "identical captions" rule).
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import av
import imageio_ffmpeg
import numpy as np
import pytest
from PIL import Image

from app.services import caption_track
from app.services.clip_service import CaptionEntry, reel_geometry
from app.services.subtitle_image_service import create_subtitle_image
from app.settings import _REPO_ANTON

# ── even_union_rect ───────────────────────────────────────────────────────────


def _contains(rect, box) -> bool:
    x0, y0, x1, y1 = rect
    bx0, by0, bx1, by1 = box
    return x0 <= bx0 and y0 <= by0 and bx1 <= x1 and by1 <= y1


@pytest.mark.parametrize(
    ("boxes", "expected"),
    [
        # odd left/top edges round DOWN, odd right/bottom edges round UP
        ([(3, 5, 9, 11)], (2, 4, 10, 12)),
        # already even: unchanged
        ([(2, 4, 10, 12)], (2, 4, 10, 12)),
        # union of several boxes
        ([(11, 921, 101, 1001), (7, 925, 99, 1013)], (6, 920, 102, 1014)),
        ([(0, 0, 1, 1)], (0, 0, 2, 2)),
    ],
)
def test_even_union_rect_known_cases(boxes, expected):
    assert caption_track.even_union_rect(boxes) == expected


def test_even_union_rect_is_even_and_contains_every_bbox():
    rng = random.Random(1137)
    for _ in range(500):
        boxes = []
        for _ in range(rng.randint(1, 6)):
            x0, y0 = rng.randint(0, 600), rng.randint(0, 1100)
            boxes.append((x0, y0, x0 + rng.randint(1, 300), y0 + rng.randint(1, 200)))
        rect = caption_track.even_union_rect(boxes)
        assert all(v % 2 == 0 for v in rect), (boxes, rect)
        assert all(_contains(rect, b) for b in boxes), (boxes, rect)
        # tight: expanding outward adds at most one pixel per edge
        assert rect[0] >= min(b[0] for b in boxes) - 1
        assert rect[1] >= min(b[1] for b in boxes) - 1
        assert rect[2] <= max(b[2] for b in boxes) + 1
        assert rect[3] <= max(b[3] for b in boxes) + 1


def test_even_union_rect_rejects_empty():
    with pytest.raises(ValueError):
        caption_track.even_union_rect([])


# ── caption_timeline ─────────────────────────────────────────────────────────


def _e(text: str, hl: int | None, start: float, dur: float) -> CaptionEntry:
    return CaptionEntry(text, hl, start, dur)


def test_timeline_fills_gaps_with_blank_and_stops_at_last_caption():
    segs = caption_track.caption_timeline(
        [_e("a b", 0, 0.25, 0.5), _e("a b", 1, 1.0, 0.25)], total_duration=5.0
    )
    assert segs == [
        caption_track.Segment(None, 0, 250_000),
        caption_track.Segment(("a b", 0), 250_000, 750_000),
        caption_track.Segment(None, 750_000, 1_000_000),
        caption_track.Segment(("a b", 1), 1_000_000, 1_250_000),
    ]


def test_timeline_boundaries_are_exact_microseconds():
    # 0.1 + 0.2 != 0.3 in floats; boundaries come from each entry's own start.
    segs = caption_track.caption_timeline(
        [_e("x", 0, 0.1, 0.2), _e("y", 0, 0.3, 0.30000000000000004)], 1.0
    )
    assert [(s.start_us, s.end_us) for s in segs] == [
        (0, 100_000),
        (100_000, 300_000),
        (300_000, 600_000),
    ]


def test_timeline_later_entry_wins_overlap_and_merges_runs():
    segs = caption_track.caption_timeline(
        [_e("a", 0, 0.0, 1.0), _e("b", 0, 0.5, 1.0), _e("a", 0, 1.5, 0.5)], 3.0
    )
    assert segs == [
        caption_track.Segment(("a", 0), 0, 500_000),
        caption_track.Segment(("b", 0), 500_000, 1_500_000),
        caption_track.Segment(("a", 0), 1_500_000, 2_000_000),
    ]


def test_timeline_skips_non_positive_and_clips_to_chapter():
    segs = caption_track.caption_timeline(
        [
            _e("late", None, 59, -58),  # legacy SRT minute bug: never visible
            _e("zero", 0, 0.5, 0.0),
            _e("tail", 0, 1.5, 2.0),  # runs past the chapter end
            _e("after", 0, 4.0, 1.0),  # starts after the chapter
        ],
        total_duration=2.0,
    )
    assert segs == [
        caption_track.Segment(None, 0, 1_500_000),
        caption_track.Segment(("tail", 0), 1_500_000, 2_000_000),
    ]


def test_timeline_empty_when_nothing_visible():
    assert caption_track.caption_timeline([], 3.0) == []
    assert caption_track.caption_timeline([_e("x", 0, 5.0, 1.0)], 3.0) == []


# ── ffconcat writer ───────────────────────────────────────────────────────────

_SEGMENTS = [
    caption_track.Segment(None, 0, 30_000),
    caption_track.Segment(("one two", 0), 30_000, 530_000),
    caption_track.Segment(("one two", 1), 530_000, 870_000),
]
_NAMES = {("one two", 0): "cap_000.png", ("one two", 1): "cap_001.png"}


def _entries(text: str) -> list[list[str]]:
    """Split an ffconcat body into per-``file`` directive blocks."""
    blocks: list[list[str]] = []
    for line in text.splitlines()[1:]:
        if line.startswith("file "):
            blocks.append([line])
        elif line:
            blocks[-1].append(line)
    return blocks


def test_ffconcat_layout(tmp_path):
    path = tmp_path / "caps.ffconcat"
    caption_track.write_ffconcat(path, _SEGMENTS, _NAMES, "blank.png", 2.0)
    text = path.read_text()
    assert text.splitlines()[0] == "ffconcat version 1.0"
    blocks = _entries(text)
    assert [b[0] for b in blocks] == [
        "file 'blank.png'",
        "file 'cap_000.png'",
        "file 'cap_001.png'",
        "file 'blank.png'",
        "file 'blank.png'",
    ]
    # relative names only (resolved against the list's own directory)
    assert all("/" not in b[0] for b in blocks)
    # every entry is a 1000 fps image so start times are not quantised to 1/25 s
    assert all(b[1] == "option framerate 1000" for b in blocks)
    assert [b[2:] for b in blocks] == [
        ["duration 0.030000"],
        ["duration 0.500000"],
        ["duration 0.340000"],
        ["duration 1.130000"],  # trailing blank runs to the chapter end ...
        [],  # ... and is repeated as the final entry
    ]


def test_ffconcat_trailing_blank_when_last_caption_reaches_the_end(tmp_path):
    path = tmp_path / "caps.ffconcat"
    segs = [caption_track.Segment(("x", 0), 0, 2_000_000)]
    caption_track.write_ffconcat(
        path, segs, {("x", 0): "cap_000.png"}, "blank.png", 2.0
    )
    blocks = _entries(path.read_text())
    assert [b[0] for b in blocks][-2:] == ["file 'blank.png'", "file 'blank.png'"]
    assert blocks[-2][2:] == ["duration 0.001000"]
    assert blocks[-1][2:] == []


def _decode_concat(list_path: Path) -> list[tuple[float, int]]:
    """Decode a caption ffconcat with the bundled ffmpeg → [(time, mean alpha)]."""
    out = list_path.parent / "decoded.mkv"
    proc = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",  # `option` directives are rejected in safe mode
            "0",
            "-i",
            str(list_path),
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "rgba",
            "-fps_mode",
            "passthrough",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    with av.open(str(out)) as container:
        return [
            (round(float(f.time), 3), int(f.to_ndarray()[..., 3].max()))
            for f in container.decode(video=0)
        ]


def test_ffconcat_in_directory_with_quote_decodes_with_ffmpeg(tmp_path):
    work = tmp_path / 'it\'s a "dir"'
    work.mkdir()
    Image.new("RGBA", (8, 4), (0, 0, 0, 0)).save(work / "blank.png")
    Image.new("RGBA", (8, 4), (255, 255, 255, 255)).save(work / "cap_000.png")
    Image.new("RGBA", (8, 4), (255, 0, 0, 255)).save(work / "cap_001.png")
    caption_track.write_ffconcat(
        work / "caps.ffconcat", _SEGMENTS, _NAMES, "blank.png", 2.0
    )
    frames = _decode_concat(work / "caps.ffconcat")
    # one frame per entry, at its exact (millisecond) start; alpha 0 = blank
    assert frames == [(0.0, 0), (0.03, 255), (0.53, 255), (0.87, 0), (2.0, 0)]


# ── build_caption_track (PNG pixels) ─────────────────────────────────────────


@pytest.fixture
def anton(monkeypatch):
    from app.services import subtitle_image_service

    monkeypatch.setattr(subtitle_image_service.settings, "font_path", str(_REPO_ANTON))


def test_build_caption_track_crops_are_pixel_equal_to_full_canvas(tmp_path, anton):
    geometry = reel_geometry(640, 360)
    entries = [
        _e("one two three", 0, 0.1, 0.4),
        _e("one two three", 1, 0.5, 0.4),
        _e("one two three", 2, 0.9, 0.4),
        _e("four", 0, 1.3, 0.5),
        _e("one two three", 1, 1.8, 0.1),  # repeat → reuses the same PNG
    ]
    track = caption_track.build_caption_track(entries, geometry, 2.5, tmp_path)
    assert track is not None
    x0, y0 = track.x, track.y
    assert (x0 % 2, y0 % 2, track.width % 2, track.height % 2) == (0, 0, 0, 0)
    assert len(track.images) == 4  # unique (text, highlight) pairs only

    canvas_w, canvas_h = geometry.canvas_size
    for (text, hl), name in track.images.items():
        full = create_subtitle_image(
            text,
            geometry.canvas_size,
            highlight_word_index=hl,
            text_anchor_y=geometry.band_anchor_y,
        )
        crop = np.asarray(Image.open(tmp_path / name))
        assert crop.shape == (track.height, track.width, 4)
        # Every non-transparent pixel of the full canvas is inside the crop ...
        alpha = full[..., 3]
        outside = alpha.copy()
        outside[max(y0, 0) : y0 + track.height, max(x0, 0) : x0 + track.width] = 0
        assert not outside.any(), (text, hl)
        # ... and the crop is that window of the full canvas, pixel for pixel
        # (rows past the canvas edge, if any, are transparent padding).
        y1 = min(y0 + track.height, canvas_h)
        x1 = min(x0 + track.width, canvas_w)
        np.testing.assert_array_equal(crop[: y1 - y0, : x1 - x0], full[y0:y1, x0:x1])
        assert not crop[y1 - y0 :, :, 3].any()
        assert not crop[:, x1 - x0 :, 3].any()

    blank = np.asarray(Image.open(tmp_path / track.blank))
    assert blank.shape == (track.height, track.width, 4) and not blank.any()
    assert (tmp_path / track.list_name).is_file()


def test_build_caption_track_none_without_visible_captions(tmp_path, anton):
    geometry = reel_geometry(640, 360)
    assert caption_track.build_caption_track([], geometry, 2.0, tmp_path) is None
    assert list(tmp_path.iterdir()) == []


# ── memory is O(1) in unique captions; cancellation between captions ─────────


def _many_unique(n_groups: int) -> list[CaptionEntry]:
    return [
        _e(f"w{g} ab cd", hl, (3 * g + hl) * 0.1, 0.1)
        for g in range(n_groups)
        for hl in range(3)
    ]


def test_build_caption_track_holds_o1_images_for_300_captions(
    tmp_path, anton, monkeypatch
):
    """Only a bounded number of caption images may be alive at once: a 1 h
    chapter has ~9k unique captions, and keeping every crop costs GBs."""
    import weakref

    alive: dict[int, weakref.ref] = {}  # PIL images are unhashable (they define __eq__)
    live_peak = 0
    real_init, real_save = Image.Image.__init__, Image.Image.save

    def tracking_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        key = id(self)
        alive[key] = weakref.ref(self, lambda _r, key=key: alive.pop(key, None))

    def counting_save(self, *args, **kwargs):
        nonlocal live_peak
        live_peak = max(live_peak, len(alive))
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "__init__", tracking_init)
    monkeypatch.setattr(Image.Image, "save", counting_save)
    geometry = reel_geometry(320, 180)
    entries = _many_unique(100)  # 300 unique (text, highlight) captions
    track = caption_track.build_caption_track(entries, geometry, 31.0, tmp_path)
    assert len(track.images) == 300
    assert live_peak <= 16, live_peak
    # only the final PNGs + blank + list are left in the work dir
    assert len(list(tmp_path.iterdir())) == 300 + 2


def test_build_caption_track_stops_between_captions_when_cancelled(tmp_path, anton):
    import threading

    from app.services import ffmpeg_tools, subtitle_image_service

    cancel = threading.Event()
    drawn = 0
    real = subtitle_image_service.create_subtitle_image

    def draw_then_cancel(*args, **kwargs):
        nonlocal drawn
        drawn += 1
        if drawn == 5:
            cancel.set()
        return real(*args, **kwargs)

    import unittest.mock as um

    with (
        um.patch.object(caption_track, "create_subtitle_image", draw_then_cancel),
        pytest.raises(ffmpeg_tools.FfmpegCancelled),
    ):
        caption_track.build_caption_track(
            _many_unique(100), reel_geometry(320, 180), 31.0, tmp_path, cancel=cancel
        )
    assert drawn < 20  # stopped promptly, not after all 300
