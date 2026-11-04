"""``clip_words`` review fixes (spec 016 Phase 4a): a corrupt words sidecar
reads as None, cached words come back in time order with invalid ones
dropped, and the reel-length sidecar round-trips and refuses bad values."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.services import clip_words
from app.services.transcription_service import WordTiming


@pytest.fixture
def clip(tmp_path) -> Path:
    folder = tmp_path / "Talk-1234" / "clips"
    folder.mkdir(parents=True)
    path = folder / "00_Talk.mp4"
    path.write_bytes(b"reel")
    return path


def _sidecar(clip: Path, content: str) -> None:
    path = clip_words.write_clip_words(str(clip), [])
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize("content", ["{not json", "", '{"word": "a"}', '[{"word": "a"}]', '[{"start": "x"}]'])
def test_a_corrupt_sidecar_reads_as_none(clip, content):
    _sidecar(clip, content)
    assert clip_words.read_clip_words(str(clip)) is None


def test_cached_words_come_back_sorted_and_invalid_ones_dropped(clip):
    _sidecar(
        clip,
        json.dumps(
            [
                {"word": "c", "start": 3.0, "end": 3.5},
                {"word": "a", "start": 1.0, "end": 1.5},
                {"word": "neg", "start": -0.5, "end": 0.2},
                {"word": "inf", "start": 1e400, "end": 1e400},
                {"word": "b", "start": 2.0, "end": 1.0},
            ]
        ),
    )
    words = clip_words.read_clip_words(str(clip))
    assert [(w.word, w.start, w.end) for w in words] == [("a", 1.0, 1.5), ("b", 2.0, 2.0), ("c", 3.0, 3.5)]


def test_out_of_window_words_are_clipped_by_the_reel_length(clip):
    clip_words.write_clip_words(str(clip), [WordTiming("in", 1.0, 2.0), WordTiming("out", 12.0, 13.0)])
    words = clip_words.read_clip_words(str(clip))
    assert [w.word for w in clip_words.clip_window_words(words, 10.0)] == ["in"]


def test_reel_duration_round_trips_and_wins_over_the_window(clip):
    assert clip_words.reel_seconds(str(clip), 100.0, 110.0) == 10.0
    assert clip_words.write_reel_duration(str(clip), 7.5) is not None
    assert clip_words.read_reel_duration(str(clip)) == 7.5
    assert clip_words.reel_seconds(str(clip), 100.0, 110.0) == 7.5
    clip_words.clear_reel_duration(str(clip))
    assert clip_words.read_reel_duration(str(clip)) is None


@pytest.mark.parametrize("seconds", [0.0, -1.0, math.inf, math.nan])
def test_a_bad_reel_duration_is_not_written(clip, seconds):
    assert clip_words.write_reel_duration(str(clip), seconds) is None
    assert clip_words.read_reel_duration(str(clip)) is None


@pytest.mark.parametrize("content", ["{oops", '{"duration": "7"}', '{"duration": -2}', "[1]", '{"duration": true}'])
def test_an_unreadable_reel_duration_falls_back_to_the_window(clip, content):
    clip_words.write_reel_duration(str(clip), 7.5)
    clip_words.reel_info_path(str(clip)).write_text(content, encoding="utf-8")
    assert clip_words.read_reel_duration(str(clip)) is None
    assert clip_words.reel_seconds(str(clip), 100.0, 110.0) == 10.0
