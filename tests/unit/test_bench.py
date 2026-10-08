"""Pure helpers of scripts/bench.py: log parsing, RSS units, compare table."""

from __future__ import annotations

import json

import pytest

from scripts.bench import (
    StageTiming,
    aggregate_stage_timings,
    compare_table,
    load_chapters,
    normalize_maxrss_mib,
    parse_stage_timing,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "[job1] Folder ready (0.01s)  dest=/tmp/x",
            StageTiming(None, "Folder ready", 0.01),
        ),
        (
            "[job1] Download complete (1.25s)  title='t'  duration=5.0s  path=/x.mp4",
            StageTiming(None, "Download complete", 1.25),
        ),
        (
            "[job1] Chapter 0  clip extracted (3.40s)  clip=/a.mp4  audio=/a.wav",
            StageTiming(0, "clip extracted", 3.40),
        ),
        (
            "[job1] Chapter 12  render done (41.07s)  output=/o.mp4",
            StageTiming(12, "render done", 41.07),
        ),
        (
            "[job1] Chapter 2  thumbnail generated (0.33s)  path=/t.jpg",
            StageTiming(2, "thumbnail generated", 0.33),
        ),
        (
            "[job1] Chapter 1  finished in 52.10s",
            StageTiming(1, "chapter total", 52.10),
        ),
        (
            "[job1] Job completed in 60.50s  outputs=1  paths=['/o.mp4']",
            StageTiming(None, "job total", 60.50),
        ),
    ],
)
def test_parse_stage_timing_recognises_orchestrator_lines(message, expected):
    assert parse_stage_timing(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        # Chapter header carries the chapter *duration* with one decimal — not a timing.
        "[job1] Chapter 0/0 start  title='Intro'  0.0–5.0s (5.0s)",
        "[job1] Dropping chapter 'x' post-clamp (duration=0.123s)",
        "[job1] Audio EOF gap: yt-dlp=5.000s safe_end=4.000s (audio shorter than video?)",
        "[job1] Chapter 0  thumbnail generated  path=/t.jpg",
        "Create Subtitle Image (640x1137, font_size=96, highlight=0, anchor_y=942)",
    ],
)
def test_parse_stage_timing_ignores_non_timing_lines(message):
    assert parse_stage_timing(message) is None


def test_aggregate_sums_stages_across_chapters():
    got = aggregate_stage_timings(
        [
            StageTiming(0, "render done", 2.0),
            StageTiming(1, "render done", 3.5),
            StageTiming(None, "Folder ready", 0.01),
        ]
    )
    assert got == {
        "Folder ready": {"total_s": 0.01, "count": 1, "per_chapter": {}},
        "render done": {
            "total_s": 5.5,
            "count": 2,
            "per_chapter": {"0": 2.0, "1": 3.5},
        },
    }


@pytest.mark.parametrize(
    ("raw", "platform", "mib"),
    [
        (1024 * 1024 * 300, "darwin", 300.0),  # macOS reports bytes
        (1024 * 300, "linux", 300.0),  # Linux reports kilobytes
        (0, "linux", 0.0),
    ],
)
def test_normalize_maxrss_mib(raw, platform, mib):
    assert normalize_maxrss_mib(raw, platform) == pytest.approx(mib)


def _result(label: str, wall: float, render: float, rss: float, pix_fmt: str) -> dict:
    return {
        "label": label,
        "wall_clock_s": wall,
        "stages": {
            "render done": {"total_s": render, "count": 1, "per_chapter": {"0": render}}
        },
        "peak_rss_mib": {"self": rss, "children": 50.0},
        "outputs": [
            {
                "name": "00_x.mp4",
                "size_bytes": 1_000_000,
                "pix_fmt": pix_fmt,
                "width": 640,
                "height": 1137,
            }
        ],
    }


def test_compare_table_renders_markdown_with_deltas():
    table = compare_table(
        _result("baseline", 10.0, 8.0, 400.0, "yuv444p"),
        _result("ffmpeg", 5.0, 2.0, 100.0, "yuv420p"),
    )
    lines = table.splitlines()
    assert lines[0] == "| metric | baseline | ffmpeg | Δ |"
    assert lines[1] == "|---|---:|---:|---:|"
    assert "| wall clock (s) | 10.00 | 5.00 | -50.0% |" in lines
    assert "| stage: render done (s) | 8.00 | 2.00 | -75.0% |" in lines
    assert "| peak RSS self (MiB) | 400.0 | 100.0 | -75.0% |" in lines
    assert "| output 00_x.mp4 pix_fmt | yuv444p | yuv420p |  |" in lines


def test_compare_table_marks_stage_missing_on_one_side():
    a = _result("a", 1.0, 1.0, 1.0, "yuv420p")
    b = _result("b", 1.0, 1.0, 1.0, "yuv420p")
    b["stages"]["thumbnail generated"] = {"total_s": 0.5, "count": 1, "per_chapter": {}}
    assert (
        "| stage: thumbnail generated (s) | — | 0.50 |  |"
        in compare_table(a, b).splitlines()
    )


def test_load_chapters_accepts_list_or_wrapped_object(tmp_path):
    spans = [
        {"title": "Intro", "start": 0, "end": 2.5},
        {"title": "Body", "start": 2.5, "end": 5},
    ]
    as_list = tmp_path / "a.json"
    as_list.write_text(json.dumps(spans))
    wrapped = tmp_path / "b.json"
    wrapped.write_text(json.dumps({"chapters": spans}))
    for path in (as_list, wrapped):
        chapters = load_chapters(path)
        assert [(c.index, c.title, c.start, c.end) for c in chapters] == [
            (0, "Intro", 0.0, 2.5),
            (1, "Body", 2.5, 5.0),
        ]


def test_load_chapters_rejects_inverted_span(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"title": "x", "start": 3, "end": 1}]))
    with pytest.raises(ValueError, match="end"):
        load_chapters(path)
