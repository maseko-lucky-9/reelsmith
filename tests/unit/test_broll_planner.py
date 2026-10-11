"""B-roll planner (FR-010, T012): where the inserts go and what they show.

``plan_broll`` is pure: clip-relative word timings in, at most two 3 s windows
out. Every boundary below is a rule of the planner; each test names the rule
it pins.
"""

from __future__ import annotations

import random

import pytest

from app.services import broll_planner
from app.services.broll_planner import PlannedBroll, plan_broll
from app.services.render_service import BrollInsert, _normalise_broll
from app.services.segment_proposer import _STOPWORDS
from app.services.transcription_service import WordTiming


def _w(word: str, start: float, length: float = 0.3) -> WordTiming:
    return WordTiming(word, start, start + length)


def _us(seconds: float) -> int:
    return round(seconds * 1_000_000)


def _windows(plan: list[PlannedBroll]) -> list[tuple[float, float, str]]:
    return [(p.start, p.duration, p.query) for p in plan]


# ── choosing the window and its query ─────────────────────────────────────────


def test_the_longest_token_wins_and_the_window_covers_it():
    words = [_w("we", 4.0), _w("saw", 4.4), _w("mountains", 10.0), _w("river", 15.0)]

    plan = plan_broll(words, 30.0)

    assert _windows(plan)[0] == (10.0, 3.0, "mountains")


def test_the_window_is_the_earliest_one_that_still_covers_the_token():
    # Candidates start on word starts; "lake" at 9.0 opens a window that
    # already holds "mountains" (10.0), so that earlier window wins the tie.
    words = [_w("lake", 9.0), _w("mountains", 10.0)]

    plan = plan_broll(words, 30.0, max_inserts=1)

    assert _windows(plan) == [(9.0, 3.0, "mountains")]


def test_equal_length_tokens_go_to_the_earliest_window():
    words = [_w("forest", 20.0), _w("rivers", 5.0)]

    plan = plan_broll(words, 40.0, max_inserts=1)

    assert _windows(plan) == [(5.0, 3.0, "rivers")]


def test_inside_one_window_the_first_spoken_of_equal_tokens_is_the_query():
    words = [_w("forest", 5.0), _w("rivers", 6.0)]

    plan = plan_broll(words, 40.0, max_inserts=1)

    assert _windows(plan) == [(5.0, 3.0, "forest")]


def test_a_word_counts_when_it_starts_inside_the_window():
    # "ocean" starts 0.01 s before the window that holds the later "tree";
    # it is not inside it, and its own window cannot start before 3.0 s.
    words = [_w("ocean", 2.99), _w("tree", 3.5)]

    plan = plan_broll(words, 30.0, max_inserts=1)

    assert _windows(plan) == [(3.0, 3.0, "tree")]


def test_the_second_window_is_the_next_best_distinct_query():
    words = [
        _w("television", 5.0),
        _w("television", 20.0),  # same query again: never a second insert
        _w("mountain", 25.0),
        _w("tree", 12.0),
    ]

    plan = plan_broll(words, 40.0)

    assert _windows(plan) == [(5.0, 3.0, "television"), (25.0, 3.0, "mountain")]


def test_windows_come_back_sorted_by_start():
    words = [_w("tree", 5.0), _w("mountains", 20.0)]

    plan = plan_broll(words, 40.0)

    assert [p.start for p in plan] == [5.0, 20.0]
    assert [p.query for p in plan] == ["tree", "mountains"]


def test_a_window_without_a_candidate_token_is_skipped():
    words = [_w("the", 5.0), _w("cat", 5.5), _w("with", 6.0), _w("their", 6.5)]

    assert plan_broll(words, 30.0) == []


# ── what counts as a query token ──────────────────────────────────────────────


@pytest.mark.parametrize("stopword", sorted(w for w in _STOPWORDS if len(w) >= 4))
def test_the_proposers_stopwords_are_never_queries(stopword):
    assert plan_broll([_w(stopword, 5.0)], 30.0) == []


@pytest.mark.parametrize(
    ("word", "query"),
    [
        ("NEMA,", "nema"),  # lowercased, punctuation dropped
        ("Wikipedia's", "wikipedia"),  # possessive dropped
        ("'Forest'", "forest"),  # quotes stripped
        ("tree", "tree"),  # 4 characters is enough
    ],
)
def test_query_tokens_are_lowercase_words(word, query):
    assert _windows(plan_broll([_w(word, 5.0)], 30.0)) == [(5.0, 3.0, query)]


@pytest.mark.parametrize("word", ["cat", "SDG5", "2024", "we've", "don't", ""])
def test_short_numeric_and_contracted_tokens_are_not_queries(word):
    assert plan_broll([_w(word, 5.0)], 30.0) == []


def test_dict_words_are_accepted():
    words = [{"word": " Mountains", "start": 6.0, "end": 6.5}]

    assert _windows(plan_broll(words, 30.0)) == [(6.0, 3.0, "mountains")]


# ── timing rules ──────────────────────────────────────────────────────────────


def test_a_window_may_start_at_exactly_three_seconds():
    assert _windows(plan_broll([_w("ocean", 3.0)], 30.0)) == [(3.0, 3.0, "ocean")]


def test_no_window_starts_before_three_seconds():
    # A keyword at 2.99 s cannot pull a window before 3.0 s; the window that
    # starts at 3.0 s does not hold it (it starts before the window).
    assert plan_broll([_w("ocean", 2.99)], 30.0) == []
    assert plan_broll([_w("ocean", 0.5)], 30.0) == []


def test_a_window_may_end_exactly_two_seconds_before_the_end():
    # duration 20: the last allowed window is [15, 18).
    assert _windows(plan_broll([_w("ocean", 15.0)], 20.0)) == [(15.0, 3.0, "ocean")]


def test_a_late_keyword_is_covered_by_the_last_allowed_window():
    assert _windows(plan_broll([_w("ocean", 17.99)], 20.0)) == [(15.0, 3.0, "ocean")]


def test_no_window_runs_into_the_last_two_seconds():
    assert plan_broll([_w("ocean", 18.0)], 20.0) == []
    assert plan_broll([_w("ocean", 19.5)], 20.0) == []


def test_windows_one_second_apart_are_both_kept():
    words = [_w("television", 5.0), _w("mountain", 9.0)]  # [5, 8) and [9, 12)

    assert _windows(plan_broll(words, 30.0)) == [
        (5.0, 3.0, "television"),
        (9.0, 3.0, "mountain"),
    ]


def test_windows_closer_than_one_second_are_not():
    words = [_w("television", 5.0), _w("mountain", 8.99)]  # gap 0.99 s

    assert _windows(plan_broll(words, 30.0)) == [(5.0, 3.0, "television")]


def test_overlapping_windows_are_never_both_kept():
    words = [_w("television", 5.0), _w("mountain", 6.0)]

    plan = plan_broll(words, 30.0)

    # [5, 8) holds both words; "mountain" has no window that avoids it.
    assert _windows(plan) == [(5.0, 3.0, "television")]


def test_a_window_on_the_other_side_also_keeps_the_gap():
    # The longer token is later; the earlier one must end 1 s before it.
    words = [_w("tree", 11.01), _w("television", 15.0)]  # [11.01, 14.01) vs [15, 18)

    assert _windows(plan_broll(words, 30.0)) == [(15.0, 3.0, "television")]

    words = [_w("tree", 11.0), _w("television", 15.0)]  # gap exactly 1.0 s

    assert _windows(plan_broll(words, 30.0)) == [
        (11.0, 3.0, "tree"),
        (15.0, 3.0, "television"),
    ]


# ── how many ──────────────────────────────────────────────────────────────────


def test_at_most_two_windows_by_default():
    words = [_w(w, t) for w, t in [("ocean", 4.0), ("forest", 10.0), ("desert", 20.0)]]

    assert len(plan_broll(words, 60.0)) == 2


@pytest.mark.parametrize("max_inserts", [0, 1, 3])
def test_max_inserts_caps_the_plan(max_inserts):
    words = [_w(w, t) for w, t in [("ocean", 4.0), ("forest", 10.0), ("desert", 20.0)]]

    assert len(plan_broll(words, 60.0, max_inserts=max_inserts)) == max_inserts


def test_insert_seconds_sets_the_window_length():
    plan = plan_broll([_w("ocean", 5.0)], 30.0, insert_seconds=2.0)

    assert _windows(plan) == [(5.0, 2.0, "ocean")]


@pytest.mark.parametrize("insert_seconds", [0.0, -1.0, float("nan"), float("inf")])
def test_insert_seconds_must_be_positive_and_finite(insert_seconds):
    with pytest.raises(ValueError, match="insert_seconds"):
        plan_broll([_w("ocean", 5.0)], 30.0, insert_seconds=insert_seconds)


def test_no_words_no_windows():
    assert plan_broll([], 60.0) == []


# ── short clips ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("duration", [0.0, 3.0, 7.99])
def test_a_clip_shorter_than_eight_seconds_gets_no_window(duration):
    words = [_w("ocean", t / 10) for t in range(0, 80, 5)]

    assert plan_broll(words, duration) == []


def test_an_eight_second_clip_gets_at_most_the_one_window_at_three_seconds():
    words = [_w("ocean", 3.2), _w("forest", 4.0), _w("mountain", 5.9)]

    assert _windows(plan_broll(words, 8.0)) == [(3.0, 3.0, "mountain")]


def test_a_twelve_second_clip_fits_two_windows():
    words = [_w("ocean", 3.0), _w("forest", 7.0)]  # [3, 6) and [7, 10); 10 = 12 - 2

    assert _windows(plan_broll(words, 12.0)) == [
        (3.0, 3.0, "ocean"),
        (7.0, 3.0, "forest"),
    ]
    assert len(plan_broll(words, 11.99)) == 1


# ── every plan is renderable ──────────────────────────────────────────────────

_VOCAB = ["ocean", "forest", "tree", "mountains", "the", "with", "cat", "television"]


@pytest.mark.parametrize("seed", range(200))
def test_every_plan_obeys_the_rules_and_validate_broll(seed):
    rng = random.Random(seed)
    duration = round(rng.uniform(0.0, 40.0), 3)
    words = sorted(
        (
            _w(rng.choice(_VOCAB), round(rng.uniform(-1.0, duration + 1.0), 3))
            for _ in range(rng.randint(0, 40))
        ),
        key=lambda w: w.start,
    )

    plan = plan_broll(words, duration)

    assert len(plan) <= 2
    assert [p.start for p in plan] == sorted(p.start for p in plan)
    assert len({p.query for p in plan}) == len(plan)
    for p in plan:
        assert p.duration == 3.0
        assert p.start >= 3.0
        assert round(p.start + p.duration, 6) <= round(duration - 2.0, 6)
        assert p.query == p.query.lower() and len(p.query) >= 4
        lo, hi = _us(p.start), _us(p.start) + _us(p.duration)
        inside = [
            t
            for w in words
            if lo <= _us(w.start) < hi
            for t in [broll_planner.query_token(w.word)]
            if t
        ]
        assert p.query in inside and len(p.query) == max(map(len, inside))
    for a, b in zip(plan, plan[1:]):
        assert round(b.start - (a.start + a.duration), 6) >= 1.0
    # The render's own timing rules (no files involved).
    _normalise_broll(
        [BrollInsert("/b.mp4", p.start, p.duration) for p in plan], duration
    )


def test_the_plan_is_deterministic():
    rng = random.Random(7)
    words = [_w(rng.choice(_VOCAB), rng.uniform(0, 60)) for _ in range(80)]

    assert plan_broll(words, 60.0) == plan_broll(list(reversed(words)), 60.0)
