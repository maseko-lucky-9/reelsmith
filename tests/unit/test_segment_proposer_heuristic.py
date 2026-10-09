"""LocalHeuristicProposer: numpy + stdlib scoring, synthetic fixtures only.

Audio is a generated 16-bit wav (loud/quiet sine sections); transcripts are
word lists built here. No librosa, webrtcvad, vaderSentiment or spaCy.
"""

from __future__ import annotations

import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

import app.services.segment_proposer as sp
from app.services.segment_proposer import (
    LocalHeuristicProposer,
    StubProposer,
    get_segment_proposer,
)
from app.settings import settings

WEIGHTS = {"hook": 0.30, "value": 0.25, "emotion": 0.15, "audio": 0.15, "trend": 0.15}
SR = 16000


@dataclass
class W:
    word: str
    start: float
    end: float


@pytest.fixture(autouse=True)
def _no_optional_nlp(monkeypatch):
    """Pin the optional NLP models to 'absent' so results do not depend on the env."""
    monkeypatch.setattr(sp, "_OPTIONAL_CACHE", {"vader": None, "spacy": None})


def _make_wav(
    path: Path, sections: list[tuple[float, float]], channels: int = 1
) -> str:
    """Write a 16-bit wav of 220 Hz sine sections given as (seconds, amplitude)."""
    chunks = []
    for secs, amp in sections:
        t = np.arange(int(secs * SR)) / SR
        chunks.append(amp * np.sin(2 * np.pi * 220 * t))
    mono = (np.concatenate(chunks) * 32767).astype(np.int16)
    data = np.repeat(mono, channels) if channels > 1 else mono
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(data.tobytes())
    return str(path)


def _speak(start: float, sentences: list[str], word_secs: float = 0.5) -> list[W]:
    """Contiguous words (no gaps); sentence ends come only from punctuation."""
    words, t = [], start
    for sentence in sentences:
        for tok in sentence.split():
            words.append(W(tok, round(t, 3), round(t + word_secs, 3)))
            t += word_secs
    return words


def _sentence_bounds(words: list[W]) -> tuple[set[float], set[float]]:
    starts = {words[0].start}
    ends = set()
    for i, w in enumerate(words):
        if w.word.endswith((".", "!", "?")):
            ends.add(w.end)
            if i + 1 < len(words):
                starts.add(words[i + 1].start)
    return starts, ends


BLAND_10 = "the cooks boil fresh noodles in a large steel pot."
BLAND_14 = "then they drain the water and stir in some butter with chopped green herbs."


# ── item 2: factory ──────────────────────────────────────────────────────────


def test_factory_local_heuristic_without_librosa_is_not_stub(monkeypatch):
    monkeypatch.setitem(sys.modules, "librosa", None)  # `import librosa` -> ImportError
    monkeypatch.setattr(settings, "segment_provider", "local_heuristic")
    proposer = get_segment_proposer()
    assert isinstance(proposer, LocalHeuristicProposer)
    assert not isinstance(proposer, StubProposer)
    assert proposer.weights == settings.score_weights_dict()
    assert proposer.min_secs == settings.target_clip_seconds_min
    assert proposer.max_secs == settings.target_clip_seconds_max


def test_factory_stub_provider_still_returns_stub(monkeypatch):
    monkeypatch.setattr(settings, "segment_provider", "stub")
    assert isinstance(get_segment_proposer(), StubProposer)


# ── item 3: short sources ─────────────────────────────────────────────────────


def test_source_shorter_than_min_secs_yields_one_full_video_segment():
    words = _speak(0.0, ["a short generated clip about noodles."])
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=20, max_secs=60)
    segs = proposer.propose(words, None, [], 8.0)
    assert len(segs) == 1
    assert (segs[0].start, segs[0].end) == (0.0, 8.0)
    assert segs[0].title == "Full Video"
    assert 0 <= segs[0].score <= 100


def test_short_source_without_words_or_audio_still_yields_full_video():
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=20, max_secs=60)
    segs = proposer.propose([], None, [], 5.0)
    assert [(s.start, s.end, s.title) for s in segs] == [(0.0, 5.0, "Full Video")]


def test_zero_duration_yields_nothing():
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=20, max_secs=60)
    assert proposer.propose([], None, [], 0.0) == []


# ── speech ratio ──────────────────────────────────────────────────────────────


def test_speech_ratio_is_fraction_of_window_covered_by_words():
    words = [W("a", 0.0, 1.0), W("b", 2.0, 3.0)]
    assert sp._speech_ratio(sp._clean_words(words, 10.0), 0.0, 4.0) == pytest.approx(
        0.5
    )


def test_speech_ratio_does_not_double_count_overlapping_words():
    words = [W("a", 0.0, 2.0), W("b", 1.0, 3.0)]
    assert sp._speech_ratio(sp._clean_words(words, 10.0), 0.0, 4.0) == pytest.approx(
        0.75
    )


def test_speech_ratio_clips_words_to_the_window():
    words = [W("a", 3.0, 6.0)]
    assert sp._speech_ratio(sp._clean_words(words, 10.0), 0.0, 4.0) == pytest.approx(
        0.25
    )


def test_clean_words_accepts_dicts_strips_text_and_clamps_to_duration():
    raw = [
        {"word": " later", "start": 2.0, "end": 2.5},
        {"word": " first", "start": 0.0, "end": 0.4},
        {"word": "   ", "start": 1.0, "end": 1.2},
        {"word": " tail", "start": 9.8, "end": 10.6},
        {"word": " gone", "start": 10.0, "end": 10.5},
    ]
    assert sp._clean_words(raw, 10.0) == [
        sp._Word("first", 0.0, 0.4),
        sp._Word("later", 2.0, 2.5),
        sp._Word("tail", 9.8, 10.0),
    ]


def test_words_near_includes_the_word_running_into_the_window():
    words = sp._clean_words(
        [W("a", 0.0, 5.5), W("b", 6.0, 7.0), W("c", 9.0, 9.5)], 20.0
    )
    starts = [w.start for w in words]
    assert [w.text for w in sp._words_near(words, starts, 5.0, 9.0)] == ["a", "b"]
    assert sp._speech_ratio(
        sp._words_near(words, starts, 5.0, 9.0), 5.0, 9.0
    ) == pytest.approx(0.375)


def test_speech_threshold_boundary():
    assert sp._has_enough_speech(0.4) is True
    assert sp._has_enough_speech(0.399) is False


def test_sparse_speech_windows_are_dropped_as_dead_air():
    dense = _speak(0.0, [BLAND_10, BLAND_14] * 3)  # 0 .. 36 s, ratio 1.0
    t0 = dense[-1].end
    # one 0.3 s word per second -> ratio ~0.3; every 0.7 s gap is a sentence end
    sparse = [W("um", round(t0 + k, 3), round(t0 + k + 0.3, 3)) for k in range(40)]
    duration = t0 + 40.0
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=20, max_secs=30)
    segs = proposer.propose(dense + sparse, None, [], duration)
    assert segs, "dense windows must survive"
    assert all(s.score_breakdown["speech"] >= 0.4 for s in segs)
    assert all(s.start < t0 for s in segs), [(s.start, s.end) for s in segs]


# ── snapping ──────────────────────────────────────────────────────────────────


def test_windows_snap_to_sentence_boundaries_from_punctuation():
    words = _speak(0.0, [BLAND_10, BLAND_14] * 5)  # 120 words, 60 s
    starts, ends = _sentence_bounds(words)
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20)
    segs = proposer.propose(words, None, [], 60.0)
    assert segs
    for s in segs:
        assert s.start in starts, s.start
        assert s.end in ends, s.end
        assert 10 <= s.end - s.start <= 20


def test_windows_snap_to_long_pauses_without_punctuation():
    words, t = [], 0.0
    pause_ends = set()
    for _block in range(6):
        for k in range(16):  # 8 s of contiguous unpunctuated words
            words.append(W("word", round(t, 3), round(t + 0.5, 3)))
            t += 0.5
        pause_ends.add(words[-1].end)
        t += 0.8  # pause >= 0.6 s marks a sentence end
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20)
    segs = proposer.propose(words, None, [], t)
    assert segs
    assert all(s.end in pause_ends for s in segs), sorted({s.end for s in segs})


def test_windows_fall_back_to_word_boundaries_without_any_sentence_end():
    words = [W("word", k * 0.5, k * 0.5 + 0.5) for k in range(100)]  # one run-on, 50 s
    word_starts = {w.start for w in words}
    word_ends = {w.end for w in words}
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20)
    segs = proposer.propose(words, None, [], 50.0)
    assert len(segs) > 1
    for s in segs:
        assert s.start in word_starts
        assert s.end in word_ends


def test_no_words_falls_back_to_a_time_grid():
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=20, max_secs=30)
    segs = proposer.propose([], None, [], 90.0)
    assert sorted((s.start, s.end) for s in segs) == [
        (0.0, 30.0),
        (30.0, 60.0),
        (60.0, 90.0),
    ]
    assert all("speech" not in s.score_breakdown for s in segs)


# ── audio energy (stdlib wave + numpy) ────────────────────────────────────────


def test_load_energy_reads_rms_from_a_wav(tmp_path):
    path = _make_wav(tmp_path / "a.wav", [(2.0, 0.8), (2.0, 0.1)])
    times, rms = sp._load_energy(path)
    assert len(times) == len(rms) > 0
    first = rms[times < 2.0].mean()
    second = rms[times >= 2.0].mean()
    assert first == pytest.approx(0.8 / np.sqrt(2), rel=0.02)
    assert second == pytest.approx(0.1 / np.sqrt(2), rel=0.02)


def test_load_energy_downmixes_stereo(tmp_path):
    path = _make_wav(tmp_path / "s.wav", [(1.0, 0.5)], channels=2)
    _times, rms = sp._load_energy(path)
    assert rms.mean() == pytest.approx(0.5 / np.sqrt(2), rel=0.02)


@pytest.mark.parametrize("bad", [None, "", "/nonexistent/x.wav"])
def test_load_energy_returns_none_when_audio_missing(bad):
    assert sp._load_energy(bad) is None


def test_load_energy_returns_none_for_a_non_wav_file(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a riff file at all")
    assert sp._load_energy(str(junk)) is None


def test_unreadable_audio_degrades_to_neutral_energy(tmp_path):
    words = _speak(0.0, [BLAND_10, BLAND_14] * 3)
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20)
    segs = proposer.propose(words, str(tmp_path / "missing.wav"), [], 36.0)
    assert segs
    assert all(s.score_breakdown["audio"] == 0.5 for s in segs)


def test_loud_section_outscores_quiet_section(tmp_path):
    words = _speak(0.0, [BLAND_10, BLAND_14] * 5)  # 60 s, same text both halves
    wav = _make_wav(tmp_path / "lq.wav", [(30.0, 0.8), (30.0, 0.05)])
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20)
    segs = proposer.propose(words, wav, [], 60.0)
    loud = [s for s in segs if s.end <= 30.0]
    quiet = [s for s in segs if s.start >= 30.0]
    assert loud and quiet
    assert min(s.score_breakdown["audio"] for s in loud) > 0.8
    assert max(s.score_breakdown["audio"] for s in quiet) < 0.2
    assert min(s.score for s in loud) > max(s.score for s in quiet)


# ── prompt overlap ────────────────────────────────────────────────────────────


def test_prompt_overlap_coverage_counts_prompt_terms_present():
    # one sentence, so focus is 1.0; coverage is 2 of 3 terms
    assert sp._prompt_overlap(
        "the bitcoin price fell", "bitcoin crash price"
    ) == pytest.approx((2 / 3 + 1.0) / 2)


def test_prompt_overlap_focus_counts_on_prompt_sentences():
    # coverage 1.0; focus is 1 of 2 sentences
    assert sp._prompt_overlap(
        "bitcoin fell today. the weather was mild.", "bitcoin"
    ) == pytest.approx((1.0 + 0.5) / 2)


def test_prompt_overlap_is_zero_when_nothing_matches():
    assert sp._prompt_overlap("the weather was mild.", "bitcoin") == 0.0


def test_prompt_overlap_matches_simple_plurals():
    assert sp._prompt_overlap("bitcoin prices rose", "price") == pytest.approx(1.0)


def test_prompt_overlap_ignores_stopwords_and_case():
    assert sp._prompt_overlap("Bitcoin rallies", "the BITCOIN of a") == pytest.approx(
        1.0
    )


def test_prompt_overlap_is_none_without_content_terms():
    assert sp._prompt_overlap("anything", None) is None
    assert sp._prompt_overlap("anything", "the and of") is None


def test_prompt_matching_window_wins():
    cooking = ["the cooks boil fresh noodles in a large steel pot."] * 4
    crypto = ["the traders watch bitcoin prices crash in a large market."] * 4
    words = _speak(0.0, cooking + crypto)  # cooking 0-20 s, crypto 20-~42 s
    duration = words[-1].end
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20)

    without = proposer.propose(words, None, [], duration)
    assert all("prompt" not in s.score_breakdown for s in without)

    segs = proposer.propose(words, None, [], duration, prompt="bitcoin crash")
    top = max(segs, key=lambda s: s.score)
    assert top.start >= 20.0
    cook = [s for s in segs if s.end <= 20.0]
    assert cook and all(s.score_breakdown["prompt"] == 0.0 for s in cook)
    assert top.score_breakdown["prompt"] == 1.0
    assert top.score > max(s.score for s in cook)


# ── weights, optional NLP, determinism ────────────────────────────────────────


def test_combine_renormalises_over_available_features():
    # emotion unavailable: weights 0.30 (hook) + 0.15 (audio) -> 0.30 / 0.45
    assert sp._combine_score({"hook": 1.0, "audio": 0.0}, WEIGHTS) == 67


def test_combine_without_any_weighted_feature_is_zero():
    assert sp._combine_score({"speech": 1.0}, WEIGHTS) == 0


def test_emotion_absent_from_breakdown_when_vader_missing():
    words = _speak(0.0, [BLAND_10, BLAND_14] * 3)
    segs = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20).propose(
        words, None, [], 36.0
    )
    assert segs
    for s in segs:
        assert "emotion" not in s.score_breakdown
        assert s.score == sp._combine_score(
            s.score_breakdown, {**sp.EXTRA_WEIGHTS, **WEIGHTS}
        )


def test_emotion_scored_when_vader_available(monkeypatch):
    class FakeVader:
        def polarity_scores(self, sentence):
            return {"compound": 0.9 if "butter" in sentence else 0.0}

    monkeypatch.setattr(sp, "_OPTIONAL_CACHE", {"vader": FakeVader(), "spacy": None})
    words = _speak(0.0, [BLAND_10, BLAND_14] * 3)
    segs = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20).propose(
        words, None, [], 36.0
    )
    assert segs and all(s.score_breakdown["emotion"] > 0 for s in segs)


def test_optional_model_is_loaded_once_and_failure_is_cached(monkeypatch):
    monkeypatch.setattr(sp, "_OPTIONAL_CACHE", {})
    calls = []

    def loader():
        calls.append(1)
        raise ImportError("not installed")

    assert sp._optional("thing", loader) is None
    assert sp._optional("thing", loader) is None
    assert len(calls) == 1


def test_output_is_deterministic(tmp_path):
    words = _speak(0.0, [BLAND_10, BLAND_14] * 5)
    wav = _make_wav(tmp_path / "d.wav", [(30.0, 0.6), (30.0, 0.2)])
    proposer = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20)
    a = proposer.propose(words, wav, [], 60.0, prompt="noodles butter")
    b = proposer.propose(words, wav, [], 60.0, prompt="noodles butter")
    assert a == b


def test_scores_and_breakdown_are_in_range(tmp_path):
    words = _speak(0.0, ["how do you boil 5 noodles?", BLAND_14] * 5)
    wav = _make_wav(tmp_path / "r.wav", [(45.0, 0.9), (15.0, 0.0)])
    segs = LocalHeuristicProposer(WEIGHTS, min_secs=10, max_secs=20).propose(
        words, wav, [], 60.0, prompt="noodles"
    )
    assert segs
    for s in segs:
        assert 0 <= s.score <= 100
        assert s.virality_score == s.score
        assert all(0.0 <= v <= 1.0 for v in s.score_breakdown.values())


def test_trend_terms_match_whole_words_only():
    proposer = LocalHeuristicProposer(WEIGHTS)
    # "ai" is a trend term; "said" and "rain" must not count as hits.
    assert proposer._trend_alignment("she said the rain stopped", ["ai"]) == 0.0
    assert proposer._trend_alignment("the new ai model", ["ai"]) > 0.0
