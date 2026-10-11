"""select_segments: score filter, greedy non-overlap pick, cap, start-time order."""

from __future__ import annotations

from app.services.segment_proposer import ProposedSegment, select_segments


def _seg(start: float, end: float, score: int) -> ProposedSegment:
    return ProposedSegment(start=start, end=end, title=f"{start}-{end}", score=score)


def test_picks_highest_scores_and_returns_them_in_start_order():
    segs = [_seg(0, 20, 50), _seg(30, 50, 90), _seg(60, 80, 70)]
    picked = select_segments(segs, max_clips=2, min_score=0)
    assert [(s.start, s.score) for s in picked] == [(30, 90), (60, 70)]


def test_drops_segments_below_min_score():
    segs = [_seg(0, 20, 39), _seg(30, 50, 80)]
    picked = select_segments(segs, max_clips=5, min_score=40)
    assert [s.score for s in picked] == [80]


def test_score_equal_to_min_score_is_kept():
    segs = [_seg(0, 20, 40)]
    picked = select_segments(segs, max_clips=5, min_score=40)
    assert [s.score for s in picked] == [40]


def test_overlapping_lower_score_segment_is_rejected():
    segs = [_seg(0, 30, 90), _seg(20, 50, 80), _seg(50, 70, 10)]
    picked = select_segments(segs, max_clips=5, min_score=0)
    assert [(s.start, s.end) for s in picked] == [(0, 30), (50, 70)]


def test_segment_touching_end_of_picked_one_is_allowed():
    # end == start is not an overlap, in either order.
    segs = [_seg(20, 40, 90), _seg(40, 60, 80), _seg(0, 20, 70)]
    picked = select_segments(segs, max_clips=5, min_score=0)
    assert [(s.start, s.end) for s in picked] == [(0, 20), (20, 40), (40, 60)]


def test_one_hundredth_of_a_second_overlap_is_rejected():
    segs = [_seg(20, 40, 90), _seg(39.99, 60, 80)]
    picked = select_segments(segs, max_clips=5, min_score=0)
    assert [(s.start, s.end) for s in picked] == [(20, 40)]


def test_segment_containing_a_picked_one_is_rejected():
    segs = [_seg(10, 20, 90), _seg(0, 60, 80)]
    picked = select_segments(segs, max_clips=5, min_score=0)
    assert [(s.start, s.end) for s in picked] == [(10, 20)]


def test_caps_at_max_clips():
    segs = [_seg(i * 30, i * 30 + 20, 100 - i) for i in range(6)]
    picked = select_segments(segs, max_clips=3, min_score=0)
    assert len(picked) == 3
    assert [s.score for s in picked] == [100, 99, 98]


def test_max_clips_zero_or_negative_returns_nothing():
    segs = [_seg(0, 20, 90)]
    assert select_segments(segs, max_clips=0, min_score=0) == []
    assert select_segments(segs, max_clips=-1, min_score=0) == []


def test_empty_input_returns_empty():
    assert select_segments([], max_clips=3, min_score=0) == []


def test_equal_scores_break_ties_by_earliest_start():
    segs = [_seg(30, 50, 80), _seg(0, 40, 80)]
    picked = select_segments(segs, max_clips=1, min_score=0)
    assert [(s.start, s.end) for s in picked] == [(0, 40)]


def test_does_not_mutate_input_order():
    segs = [_seg(30, 50, 10), _seg(0, 20, 90)]
    select_segments(segs, max_clips=2, min_score=0)
    assert [s.start for s in segs] == [30, 0]
