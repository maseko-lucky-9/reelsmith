"""Pure helpers behind clip discovery for sources without chapters (FR-009)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.models import PipelineOptions
from app.services import segment_discovery as sd
from app.services.segment_proposer import ProposedSegment
from app.services.transcription_service import WordTiming


# ── rebase_words ──────────────────────────────────────────────────────────────


def test_rebase_words_shifts_into_window_time():
    words = [WordTiming("a", 10.0, 10.5), WordTiming("b", 11.0, 11.4)]

    rebased = sd.rebase_words(words, 10.0, 20.0)

    assert [(w.word, w.start, w.end) for w in rebased] == [
        ("a", 0.0, 0.5),
        ("b", 1.0, pytest.approx(1.4)),
    ]


def test_rebase_words_drops_words_outside_the_window():
    words = [
        WordTiming("before", 1.0, 2.0),
        WordTiming("touching-start", 3.0, 5.0),  # ends exactly at the window start
        WordTiming("inside", 6.0, 7.0),
        WordTiming("touching-end", 9.0, 9.5),  # starts exactly at the window end
        WordTiming("after", 12.0, 13.0),
    ]

    rebased = sd.rebase_words(words, 5.0, 9.0)

    assert [w.word for w in rebased] == ["inside"]


def test_rebase_words_clamps_words_straddling_the_edges():
    words = [WordTiming("left", 4.5, 5.5), WordTiming("right", 8.5, 9.8)]

    rebased = sd.rebase_words(words, 5.0, 9.0)

    assert [(w.word, w.start, w.end) for w in rebased] == [
        ("left", 0.0, 0.5),
        ("right", 3.5, 4.0),
    ]
    assert all(0.0 <= w.start <= w.end <= 4.0 for w in rebased)


def test_rebase_words_accepts_dicts_and_leaves_the_input_alone():
    words = [{"word": "hi", "start": 2.0, "end": 2.5}]

    rebased = sd.rebase_words(words, 1.0, 3.0)

    assert [(w.word, w.start, w.end) for w in rebased] == [("hi", 1.0, 1.5)]
    assert words == [{"word": "hi", "start": 2.0, "end": 2.5}]


# ── words sidecar ─────────────────────────────────────────────────────────────


def test_sidecar_path_sits_next_to_the_source(tmp_path: Path):
    video = tmp_path / "my.talk.mp4"

    assert sd.words_sidecar_path(str(video)) == tmp_path / "my.talk.words.json"


def test_sidecar_round_trip_is_a_list_of_word_dicts(tmp_path: Path):
    video = tmp_path / "video.mp4"
    words = [WordTiming("hello", 0.0, 0.4), WordTiming("world", 0.5, 0.9)]

    path = sd.write_words_sidecar(str(video), words)

    assert json.loads(path.read_text()) == [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.5, "end": 0.9},
    ]
    assert sd.read_words_sidecar(str(video)) == words
    # Atomic write: only the sidecar is left behind.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["video.words.json"]


def test_read_sidecar_missing_or_corrupt_is_none(tmp_path: Path):
    video = tmp_path / "video.mp4"
    assert sd.read_words_sidecar(str(video)) is None

    sd.words_sidecar_path(str(video)).write_text("{not json")
    assert sd.read_words_sidecar(str(video)) is None

    sd.words_sidecar_path(str(video)).write_text('{"word": "x"}')
    assert sd.read_words_sidecar(str(video)) is None


# ── selection: relative bar, clip budget, coverage cap ────────────────────────


def _seg(score: int, start: float = 0.0, length: float = 30.0) -> ProposedSegment:
    return ProposedSegment(start=start, end=start + length, score=score)


def test_relative_min_score_is_sixty_percent_of_the_best_rounded_up():
    # PR #43's heuristic scores land around 13-38 on a real talk; a fixed bar
    # such as 50 would drop every one of these.
    assert sd.relative_min_score([_seg(38), _seg(30), _seg(14)]) == 23  # 22.8 -> 23
    assert sd.relative_min_score([_seg(30), _seg(10)]) == 18  # exactly 18.0


def test_relative_min_score_has_a_floor_of_zero():
    assert sd.relative_min_score([]) == 0
    assert sd.relative_min_score([_seg(0), _seg(0)]) == 0


@pytest.mark.parametrize(
    ("duration", "budget"),
    [
        (0.0, 1),
        (59.0, 1),  # 0.49 + 0.5 -> 0, clamped up to 1
        (60.0, 1),
        (179.0, 1),  # 1.49
        (180.0, 2),  # 1.5 rounds half up
        (181.0, 2),
        (239.0, 2),
        (240.0, 2),
        (241.0, 2),
        (299.0, 2),  # 2.49
        (300.0, 3),  # 2.5: half up (Python's round() would give 2)
        (539.0, 4),
        (540.0, 5),  # 4.5
        (600.0, 5),  # 5.5 -> 5, the cap
        (3600.0, 5),
    ],
)
def test_clip_budget_is_one_clip_per_two_minutes(duration, budget):
    assert sd.clip_budget(duration) == budget


def test_select_keeps_a_score_at_exactly_the_bar_and_drops_one_below():
    # Long source (budget 5, cap 300 s): only the bar decides.
    segments = [_seg(30, 0.0), _seg(18, 40.0), _seg(17, 80.0)]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [s.score for s in picked] == [30, 18]


def test_select_honours_the_clip_budget():
    segments = [_seg(30 - i, 40.0 * i) for i in range(6)]

    assert [s.score for s in sd.select_discovered(segments, duration=600.0)] == [
        30, 29, 28, 27, 26,
    ]
    assert [s.score for s in sd.select_discovered(segments, duration=236.0)] == [30, 29]
    assert [s.score for s in sd.select_discovered(segments, duration=60.0)] == [30]


def test_coverage_of_exactly_half_the_source_is_allowed():
    # 600 s source -> cap 300 s; 200 + 100 = 300 exactly.
    segments = [_seg(30, 0.0, 200.0), _seg(29, 250.0, 100.0)]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [s.score for s in picked] == [30, 29]


def test_coverage_past_half_the_source_skips_the_candidate():
    # 200 + 100.5 > 300: the second is skipped, a shorter later one still fits.
    segments = [
        _seg(30, 0.0, 200.0),
        _seg(29, 250.0, 100.5),
        _seg(28, 400.0, 90.0),
    ]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [s.score for s in picked] == [30, 28]


def test_the_best_segment_is_kept_even_past_the_coverage_cap():
    # One 50 s clip of a 60 s source is 83% coverage: still kept.
    segments = [_seg(30, 5.0, 50.0), _seg(29, 0.0, 4.0)]

    picked = sd.select_discovered(segments, duration=60.0)

    assert [s.score for s in picked] == [30]


def test_select_skips_overlaps_but_allows_touching_segments():
    segments = [_seg(30, 0.0), _seg(29, 10.0), _seg(28, 30.0)]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [(s.start, s.end) for s in picked] == [(0.0, 30.0), (30.0, 60.0)]


def test_select_returns_start_order_and_nothing_for_no_candidates():
    segments = [_seg(20, 300.0), _seg(30, 0.0)]

    assert [s.start for s in sd.select_discovered(segments, duration=600.0)] == [
        0.0, 300.0,
    ]
    assert sd.select_discovered([], duration=600.0) == []


# ── clip length range ─────────────────────────────────────────────────────────


def test_clip_length_range_defaults_to_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sd.settings, "target_clip_seconds_min", 20)
    monkeypatch.setattr(sd.settings, "target_clip_seconds_max", 60)

    assert sd.clip_length_range(PipelineOptions()) == (20, 60)


def test_clip_length_range_prefers_the_job_options(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sd.settings, "target_clip_seconds_min", 20)
    monkeypatch.setattr(sd.settings, "target_clip_seconds_max", 60)

    opts = PipelineOptions(target_length_min_seconds=15, target_length_max_seconds=45)
    assert sd.clip_length_range(opts) == (15, 45)
    # A min above the configured max widens the max instead of inverting.
    assert sd.clip_length_range(PipelineOptions(target_length_min_seconds=90)) == (
        90,
        90,
    )


# ── chapters from segments ────────────────────────────────────────────────────


def test_segments_to_chapters_matches_the_chapter_shape():
    segments = [
        ProposedSegment(
            start=10.0,
            end=40.0,
            title="Why it works",
            summary="s1",
            score=31,
            score_breakdown={"hook": 0.5},
        ),
        ProposedSegment(start=50.0, end=95.0, title="", summary="s2", score=20),
    ]

    chapters = sd.segments_to_chapters(segments, safe_end=90.0)

    assert chapters == [
        {
            "index": 0,
            "title": "Why it works",
            "start": 10.0,
            "end": 40.0,
            "virality_score": 31,
            "score_breakdown": {"hook": 0.5},
            "summary": "s1",
        },
        {
            "index": 1,
            "title": "Clip 2",
            "start": 50.0,
            "end": 90.0,
            "virality_score": 20,
            "score_breakdown": {},
            "summary": "s2",
        },
    ]


def test_segments_to_chapters_drops_windows_past_the_safe_end():
    segments = [
        ProposedSegment(start=0.0, end=30.0, score=10),
        ProposedSegment(start=89.8, end=120.0, score=12),
    ]

    chapters = sd.segments_to_chapters(segments, safe_end=90.0)

    assert [(c["index"], c["start"], c["end"]) for c in chapters] == [(0, 0.0, 30.0)]


# ── proposer factory honours the job's clip length range ─────────────────────


def test_get_segment_proposer_takes_the_clip_length_range(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services.segment_proposer import (
        LocalHeuristicProposer,
        get_segment_proposer,
    )

    monkeypatch.setattr(sd.settings, "segment_provider", "local_heuristic")
    monkeypatch.setattr(sd.settings, "target_clip_seconds_min", 20)
    monkeypatch.setattr(sd.settings, "target_clip_seconds_max", 60)

    default = get_segment_proposer()
    custom = get_segment_proposer(min_secs=15, max_secs=45)

    assert isinstance(custom, LocalHeuristicProposer)
    assert (default.min_secs, default.max_secs) == (20, 60)
    assert (custom.min_secs, custom.max_secs) == (15, 45)


# ── redundancy penalty (near-duplicate clips) ─────────────────────────────────


def _tseg(score: int, start: float, text: str, length: float = 30.0) -> ProposedSegment:
    return ProposedSegment(start=start, end=start + length, score=score, text=text)


# Five / twelve distinct content words (>= 3 chars, not stopwords).
FIVE = "alpha bravo charlie delta echo"
OTHER = "kilo lima mike november oscar"
TWELVE = "one1 two2 three3 four4 five5 six6 seven7 eight8 nine9 ten10 eleven11 twelve12"


def test_content_words_reuse_the_proposer_tokens_and_drop_short_ones():
    words = sd.content_words("The Cats sat on a mat, and it is OK. Dogs' bark!")

    # "the/and/it/is/on/a" are stopwords; "ok" is < 3 chars; plurals stripped.
    assert words == {"cat", "sat", "mat", "dog", "bark"}


def test_redundancy_is_the_share_of_the_candidates_words_already_kept():
    kept = [sd.content_words("alpha bravo charlie"), sd.content_words("delta")]

    assert sd.redundancy(sd.content_words(FIVE), kept) == pytest.approx(0.6)
    assert sd.redundancy(sd.content_words(FIVE), []) == 0.0
    assert sd.redundancy(set(), kept) == 0.0


def test_identical_text_is_skipped_for_a_novel_lower_score():
    segments = [
        _tseg(30, 0.0, FIVE),
        _tseg(29, 100.0, FIVE),  # word-for-word repeat
        _tseg(20, 200.0, OTHER),
    ]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [s.score for s in picked] == [30, 20]


def test_half_shared_words_fall_below_the_bar_on_the_adjusted_score():
    # 29 * (1 - 0.5) = 14.5 < ceil(0.6 * 30) = 18: dropped, although its
    # original score clears the bar.
    segments = [
        _tseg(30, 0.0, "alpha bravo charlie delta"),
        _tseg(29, 100.0, "alpha bravo xray yankee"),
    ]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [s.score for s in picked] == [30]


def test_a_partly_shared_candidate_loses_to_a_novel_lower_scored_one():
    # B shares 1 of 4 words with A: adjusted 29 * 0.75 = 21.75 < C's 22.
    segments = [
        _tseg(30, 0.0, "alpha bravo charlie delta"),
        _tseg(29, 100.0, "alpha xray yankee zulu"),
        _tseg(22, 200.0, OTHER),
    ]

    # Budget 2 (240 s): the novel C takes the second slot.
    assert [s.score for s in sd.select_discovered(segments, duration=240.0)] == [30, 22]
    # Budget 3 (600 s): B still survives with its reduced score.
    assert [s.score for s in sd.select_discovered(segments, duration=600.0)] == [
        30, 29, 22,
    ]


def test_redundancy_at_the_skip_threshold_is_skipped():
    # All scores 0, so the bar is 0 and only REDUNDANCY_SKIP can stop a repeat.
    segments = [
        _tseg(0, 0.0, FIVE),
        _tseg(0, 100.0, "alpha bravo charlie xray yankee"),  # 3/5 = 0.6
    ]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [s.start for s in picked] == [0.0]


def test_redundancy_just_below_the_skip_threshold_is_kept():
    shared = " ".join(TWELVE.split()[:7])
    segments = [
        _tseg(0, 0.0, TWELVE),
        _tseg(0, 100.0, shared + " xray yankee zulu quebec romeo"),  # 7/12 = 0.583
    ]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [s.start for s in picked] == [0.0, 100.0]


def test_empty_text_counts_as_novel():
    segments = [_tseg(30, 0.0, FIVE), _tseg(29, 100.0, "")]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [s.score for s in picked] == [30, 29]


def test_the_best_clip_is_not_penalised_by_overlapping_neighbours():
    # Sliding windows around the best share its words; they overlap it, so
    # they are never kept, and they must not count against it either.
    segments = [
        _tseg(30, 0.0, FIVE),
        _tseg(29, 10.0, FIVE),
        _tseg(28, 20.0, FIVE),
        _tseg(20, 200.0, OTHER),
    ]

    picked = sd.select_discovered(segments, duration=600.0)

    assert [(s.start, s.score) for s in picked] == [(0.0, 30), (200.0, 20)]


def test_ties_on_the_adjusted_score_go_to_the_earliest_start():
    segments = [
        _tseg(30, 0.0, FIVE),
        _tseg(20, 300.0, "kilo lima"),
        _tseg(20, 200.0, "mike november"),
    ]

    picked = sd.select_discovered(segments, duration=240.0)  # budget 2

    assert [s.start for s in picked] == [0.0, 200.0]


def test_the_heuristic_proposer_carries_the_full_window_text():
    from app.services.segment_proposer import LocalHeuristicProposer

    words = [
        WordTiming(f"sentenceword{i}.", i * 1.0, i * 1.0 + 0.5) for i in range(60)
    ]
    segs = LocalHeuristicProposer(
        weights={"hook": 1.0}, min_secs=20, max_secs=30
    ).propose(words, None, [], 60.0)

    assert segs
    for seg in segs:
        expected = " ".join(w.word for w in words if seg.start <= w.start < seg.end)
        assert seg.text == expected
        assert len(seg.text) > 200  # longer than the 200-char summary
