"""Unit tests for transcription_service — all use the stub provider."""
from __future__ import annotations

import pytest

from app.services.transcription_service import WordTiming, speech_to_text, transcribe_to_words


@pytest.fixture(autouse=True)
def use_stub(monkeypatch):
    from app import settings as s
    monkeypatch.setattr(s.settings, "transcription_provider", "stub")


def test_transcribe_to_words_returns_word_timing_list():
    words = transcribe_to_words("/fake/audio.wav")
    assert isinstance(words, list)
    assert len(words) > 0
    assert all(isinstance(w, WordTiming) for w in words)


def test_word_timing_fields_are_correct_types():
    words = transcribe_to_words("/fake/audio.wav")
    for w in words:
        assert isinstance(w.word, str)
        assert isinstance(w.start, float)
        assert isinstance(w.end, float)
        assert w.start >= 0
        assert w.end > w.start


def test_stub_words_are_deterministic():
    first = transcribe_to_words("/fake/a.wav")
    second = transcribe_to_words("/fake/b.wav")
    assert [w.word for w in first] == [w.word for w in second]


def test_speech_to_text_joins_words():
    text = speech_to_text("/fake/audio.wav")
    assert isinstance(text, str)
    assert len(text) > 0
    words = transcribe_to_words("/fake/audio.wav")
    assert text == " ".join(w.word for w in words)


def test_speech_to_text_word_count_matches():
    text = speech_to_text("/fake/audio.wav")
    words = transcribe_to_words("/fake/audio.wav")
    assert len(text.split()) == len(words)
