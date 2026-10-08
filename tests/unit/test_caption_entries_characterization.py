"""Characterization of the caption schedule and ``probe_safe_end``.

P0 locked the MoviePy-era behaviour of ``clip_service.add_captions_to_clip``
(word-timing loop and SRT-caption loop) and ``clip_service.probe_safe_end``.
P1 deleted the MoviePy compositor; its schedule now lives in the pure
``clip_service.caption_entries``, which these tests pin to the SAME literal
tables (unchanged since P0), so the ffmpeg port provably schedules identical
captions.

Each case is checked two ways:

* ``EXPECTED_*`` literal tables — the contract, readable at a glance;
* ``_reference_word_schedule`` / ``_reference_srt_schedule`` — a pure,
  test-local mirror of the legacy loops, kept as an independent oracle;
* ``clip_service.caption_entries`` — the production port.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pysrt
import pytest

from app.services import clip_service
from app.services.transcription_service import WordTiming
from tests.fixtures.make_sync_fixture import SyncFixture

SAMPLE_MP4 = Path(__file__).resolve().parents[1] / "fixtures" / "sample.mp4"


@dataclass(frozen=True)
class Entry:
    text: str
    highlight: int | None
    start: float
    duration: float


def _w(word: str, start: float, end: float) -> WordTiming:
    return WordTiming(word=word, start=start, end=end)


# ── Test-local reference (mirrors the legacy clip_service.py:198-227) ─────────


def _reference_word_schedule(words: list[WordTiming], n: int) -> list[Entry]:
    entries: list[Entry] = []
    for i, word in enumerate(words):
        group_start = (i // n) * n
        group_text = " ".join(w.word for w in words[group_start : group_start + n])
        clip_end = words[i + 1].start if i + 1 < len(words) else word.end
        duration = clip_end - word.start
        if duration <= 0:
            continue
        entries.append(Entry(group_text, i % n, word.start, duration))
    return entries


def _reference_srt_schedule(captions: pysrt.SubRipFile) -> list[Entry]:
    # NB: SubRipTime.seconds is the 0-59 seconds *component*, not total seconds.
    return [
        Entry(c.text, None, c.start.seconds, c.end.seconds - c.start.seconds)
        for c in captions
    ]


# ── Production port under test ────────────────────────────────────────────────


def _port(
    *,
    word_timings: list[WordTiming] | None = None,
    captions=None,
    n: int = 3,
) -> list[Entry]:
    return [
        Entry(e.text, e.highlight, e.start, e.duration)
        for e in clip_service.caption_entries(word_timings, captions, n)
    ]


def _assert_entries(got: list[Entry], want: list[Entry]) -> None:
    assert [(e.text, e.highlight) for e in got] == [(e.text, e.highlight) for e in want]
    for g, w in zip(got, want):
        assert g.start == pytest.approx(w.start, abs=1e-9), (g, w)
        assert g.duration == pytest.approx(w.duration, abs=1e-9), (g, w)


# ── Word-timing (karaoke) schedule cases ─────────────────────────────────────

WORD_CASES: dict[str, tuple[list[WordTiming], int, list[Entry]]] = {
    # Each word is shown until the NEXT word starts (gaps are bridged);
    # the last word ends at its own end. Highlight = position in its group.
    "gaps_bridged_to_next_start": (
        [
            _w("one", 0.0, 0.4),
            _w("two", 0.5, 0.9),
            _w("three", 1.0, 1.3),
            _w("four", 1.6, 2.0),
            _w("five", 2.1, 2.6),
        ],
        3,
        [
            Entry("one two three", 0, 0.0, 0.5),
            Entry("one two three", 1, 0.5, 0.5),
            Entry("one two three", 2, 1.0, 0.6),
            Entry("four five", 0, 1.6, 0.5),
            Entry("four five", 1, 2.1, 0.5),
        ],
    ),
    # Overlapping words are cut at the next word's start.
    "overlap_truncated_at_next_start": (
        [_w("fast", 0.0, 0.8), _w("talk", 0.3, 0.9)],
        3,
        [Entry("fast talk", 0, 0.0, 0.3), Entry("fast talk", 1, 0.3, 0.6)],
    ),
    # Zero-duration slots are skipped but still occupy their group position,
    # so the group text keeps the skipped word and highlights do not shift.
    "zero_duration_skipped_keeps_group_positions": (
        [_w("a", 0.0, 0.3), _w("b", 0.5, 0.5), _w("c", 0.5, 0.9), _w("d", 1.0, 1.4)],
        3,
        [
            Entry("a b c", 0, 0.0, 0.5),
            Entry("a b c", 2, 0.5, 0.5),
            Entry("d", 0, 1.0, 0.4),
        ],
    ),
    # Non-monotonic starts give a negative slot → skipped (no clamping).
    "negative_slot_skipped": (
        [_w("x", 1.0, 1.5), _w("y", 0.8, 1.2), _w("z", 1.3, 1.6)],
        3,
        [Entry("x y z", 1, 0.8, 0.5), Entry("x y z", 2, 1.3, 0.30000000000000004)],
    ),
    # Last word with zero length is skipped.
    "zero_length_last_word_skipped": (
        [_w("hi", 0.0, 0.4), _w("there", 0.6, 0.6)],
        3,
        [Entry("hi there", 0, 0.0, 0.6)],
    ),
    "one_word_per_group": (
        [_w("solo", 0.25, 0.75), _w("act", 0.75, 1.5)],
        1,
        [Entry("solo", 0, 0.25, 0.5), Entry("act", 0, 0.75, 0.75)],
    ),
    "four_word_groups": (
        [_w(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate("a b c d e".split())],
        4,
        [
            Entry("a b c d", 0, 0.0, 0.5),
            Entry("a b c d", 1, 0.5, 0.5),
            Entry("a b c d", 2, 1.0, 0.5),
            Entry("a b c d", 3, 1.5, 0.5),
            Entry("e", 0, 2.0, 0.4),
        ],
    ),
    "empty_word_list": ([], 3, []),
}


@pytest.mark.parametrize("case", sorted(WORD_CASES))
def test_reference_word_schedule_matches_expected_table(case: str):
    words, n, expected = WORD_CASES[case]
    _assert_entries(_reference_word_schedule(words, n), expected)


@pytest.mark.parametrize("case", sorted(WORD_CASES))
def test_caption_entries_word_schedule_matches_expected_table(case: str):
    words, n, expected = WORD_CASES[case]
    got = _port(word_timings=words, n=n)
    _assert_entries(got, expected)
    assert len(got) == len(expected)


def test_word_timings_take_precedence_over_caption_list():
    srt = pysrt.SubRipFile(
        items=[
            pysrt.SubRipItem(
                1,
                start=pysrt.SubRipTime(seconds=0),
                end=pysrt.SubRipTime(seconds=3),
                text="IGNORED",
            )
        ]
    )
    _assert_entries(
        _port(word_timings=[_w("kept", 0.0, 1.0)], captions=srt),
        [Entry("kept", 0, 0.0, 1.0)],
    )


def test_empty_word_list_suppresses_srt_captions():
    """The orchestrator always passes ``word_timings=words`` — an empty list,
    not None, when transcription is off — so the SRT branch never runs there."""
    srt = pysrt.SubRipFile(
        items=[
            pysrt.SubRipItem(
                1,
                start=pysrt.SubRipTime(seconds=0),
                end=pysrt.SubRipTime(seconds=3),
                text="IGNORED",
            )
        ]
    )
    assert _port(word_timings=[], captions=srt) == []


# ── SRT caption-list path (word_timings=None) ────────────────────────────────


def _srt(*spans: tuple[float, float, str]) -> pysrt.SubRipFile:
    return pysrt.SubRipFile(
        items=[
            pysrt.SubRipItem(
                i + 1,
                start=pysrt.SubRipTime(seconds=s),
                end=pysrt.SubRipTime(seconds=e),
                text=t,
            )
            for i, (s, e, t) in enumerate(spans)
        ]
    )


SRT_CASES: dict[str, tuple[pysrt.SubRipFile, list[Entry]]] = {
    # LEGACY BUG, locked deliberately: ``caption.start.seconds`` is pysrt's
    # 0-59 seconds component, so milliseconds are truncated ...
    "sub_second_truncated": (
        _srt((0.2, 2.4, "a b c"), (2.5, 3.9, "d e")),
        [Entry("a b c", None, 0, 2), Entry("d e", None, 2, 1)],
    ),
    # ... and minutes are dropped, giving a negative duration across a
    # minute boundary (passed through unclamped).
    "minute_component_dropped": (
        _srt((59.5, 61.25, "late"), (62.0, 63.0, "later")),
        [Entry("late", None, 59, -58), Entry("later", None, 2, 1)],
    ),
}


@pytest.mark.parametrize("case", sorted(SRT_CASES))
def test_reference_srt_schedule_matches_expected_table(case: str):
    captions, expected = SRT_CASES[case]
    _assert_entries(_reference_srt_schedule(captions), expected)


@pytest.mark.parametrize("case", sorted(SRT_CASES))
def test_caption_entries_srt_schedule_matches_expected_table(case: str):
    captions, expected = SRT_CASES[case]
    _assert_entries(_port(word_timings=None, captions=captions), expected)


def test_srt_schedule_from_written_file(tmp_path):
    """Round-trip through caption_service + render_service._load_captions."""
    from app.services import caption_service, render_service

    words = [
        _w("one", 0.2, 0.9),
        _w("two", 1.1, 1.5),
        _w("three", 1.6, 2.4),
        _w("four", 2.5, 3.75),
    ]
    path = tmp_path / "c.srt"
    caption_service.write_captions(
        caption_service.generate_captions_from_word_timings(words, 3, "srt"),
        "srt",
        str(path),
    )
    _assert_entries(
        _port(captions=render_service._load_captions(str(path))),
        [Entry("one two three", None, 0, 2), Entry("four", None, 2, 1)],
    )


# ── Canvas size and caption anchor (legacy clip_service.py:186-195) ───────────


@pytest.mark.parametrize(
    ("clip_size", "canvas", "anchor"),
    [
        ((640, 360), (640, 1137), 942),
        ((1280, 720), (1280, 2275), 1886),
        ((1920, 1080), (1920, 3413), 2829),
        ((320, 240), (320, 568), 486),
    ],
)
def test_reel_canvas_size_and_band_anchor(clip_size, canvas, anchor):
    geometry = clip_service.reel_geometry(*clip_size, 9 / 16)
    assert geometry.canvas_size == canvas
    assert geometry.band_anchor_y == anchor


# ── probe_safe_end (legacy clip_service.py:23-34) ─────────────────────────────


def _fake_duration(v_dur: float, a_dur: float | None):
    def duration(_path, kind="container"):
        return {"video": v_dur, "audio": a_dur, "container": v_dur}[kind]

    return duration


@pytest.mark.parametrize(
    ("v_dur", "a_dur", "expected"),
    [
        (10.0, 8.5, 7.5),  # audio shorter → audio wins
        (8.0, 12.0, 7.0),  # video shorter → video wins
        (10.0, None, 9.0),  # no audio stream → video duration
        (0.6, 0.6, 0.0),  # shorter than the epsilon → clamped to 0
    ],
)
def test_probe_safe_end_logic(monkeypatch, v_dur, a_dur, expected):
    monkeypatch.setattr(
        clip_service.ffmpeg_tools, "duration", _fake_duration(v_dur, a_dur)
    )
    assert clip_service.AUDIO_TAIL_EPSILON_SECONDS == 1.0
    assert clip_service.probe_safe_end("ignored.mp4") == pytest.approx(
        expected, abs=1e-12
    )


def test_probe_safe_end_on_sample_mp4():
    # MoviePy reported exactly 4.0 (10 ms banner resolution); PyAV reads the
    # stream durations exactly. Loosened deliberately to the same 11 ms bound
    # as the sync fixtures below.
    assert clip_service.probe_safe_end(str(SAMPLE_MP4)) == pytest.approx(
        4.0, abs=0.011
    )


def test_probe_safe_end_on_sync_fixtures(
    sync_fixture_640: SyncFixture, sync_fixture_720: SyncFixture
):
    # MoviePy reads duration from ffmpeg's banner at 10 ms resolution:
    # 144 frames @ 23.976 = 6.006 s → "6.01"; 72 frames = 3.003 s → "3.00".
    # The P1 PyAV port reads exact stream durations (6.006 s → 5.006,
    # 3.003 s → 2.003); abs=0.011 is the deliberate loosening.
    assert clip_service.probe_safe_end(str(sync_fixture_640.path)) == pytest.approx(
        5.01, abs=0.011
    )
    assert clip_service.probe_safe_end(str(sync_fixture_720.path)) == pytest.approx(
        2.0, abs=0.011
    )
