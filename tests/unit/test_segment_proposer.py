"""Unit tests for segment proposer scoring features."""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass

from app.services.segment_proposer import (
    LocalHeuristicProposer,
    StubProposer,
    filter_word_timings,
    get_segment_proposer,
    _extract_title,
    _load_trends,
)
from app.settings import settings


@dataclass
class FakeWord:
    word: str
    start: float
    end: float


FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "segments" / "sample_transcript.json").read_text()
)
WORDS = [FakeWord(**w) for w in FIXTURE["words"]]
DURATION = FIXTURE["duration"]

_DEFAULT_WEIGHTS = {"hook": 0.30, "value": 0.25, "emotion": 0.15, "audio": 0.15, "trend": 0.15}
_CHAPTERS = [{"start": 0.0, "end": 30.0}]


def test_filter_word_timings_basic():
    filtered = filter_word_timings(WORDS, 0.0, 3.0)
    assert all(w.start < 3.0 for w in filtered)
    assert all(w.start >= 0.0 for w in filtered)


def test_filter_word_timings_window():
    filtered = filter_word_timings(WORDS, 5.0, 10.0)
    assert all(w.start >= 5.0 for w in filtered)
    words_text = [w.word for w in filtered]
    assert "This" in words_text


def test_stub_proposer_returns_one_segment():
    proposer = StubProposer()
    result = proposer.propose(WORDS, "/fake/audio.wav", _CHAPTERS, DURATION)
    assert len(result) == 1
    assert result[0].score == 42


def test_extract_title_takes_first_sentence():
    title = _extract_title("Hello world. This is a test.")
    assert title == "Hello world"


def test_trends_load():
    trends = _load_trends()
    assert isinstance(trends, list)
    assert len(trends) > 0


def test_get_segment_proposer_returns_stub_for_chapter_mode(monkeypatch):
    monkeypatch.setattr(settings, "segment_provider", "chapter")
    proposer = get_segment_proposer()
    assert isinstance(proposer, StubProposer)


def test_heuristic_proposer_builds_candidates():
    proposer = LocalHeuristicProposer(weights=_DEFAULT_WEIGHTS, min_secs=5, max_secs=30)
    candidates = proposer._build_candidates(_CHAPTERS, DURATION)
    assert len(candidates) > 0
    for start, end in candidates:
        assert end - start >= 5


def test_heuristic_trend_alignment():
    proposer = LocalHeuristicProposer(weights=_DEFAULT_WEIGHTS)
    trends = _load_trends()
    score = proposer._trend_alignment("how to make money online in 5 steps tutorial", trends)
    assert 0.0 <= score <= 1.0
    assert score > 0


def test_heuristic_emotional_flow_zero_for_empty():
    proposer = LocalHeuristicProposer(weights=_DEFAULT_WEIGHTS)
    score = proposer._emotional_flow("")
    assert score == 0.0


def test_heuristic_hook_strength_no_rms():
    proposer = LocalHeuristicProposer(weights=_DEFAULT_WEIGHTS)
    score = proposer._hook_strength("how to earn money", WORDS[:3], None, 0.0, 5.0)
    assert 0.0 <= score <= 1.0


def test_heuristic_audio_engagement_no_rms():
    proposer = LocalHeuristicProposer(weights=_DEFAULT_WEIGHTS)
    score = proposer._audio_engagement(None, 0.0, 30.0)
    assert score == 0.0
