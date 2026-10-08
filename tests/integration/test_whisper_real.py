"""Real faster-whisper transcription through ``transcription_service``.

Loads the actual ``base`` model (CPU, int8; ~145 MB from the Hugging Face hub on
first run, cached under ``~/.cache/huggingface``) and transcribes a committed
speech clip. Guards the transcription stack against dependency drift that the
stub-provider unit tests can't see, e.g. PyAV 19 breaking faster-whisper 1.2.1's
audio decode (``open() got an unexpected keyword argument 'metadata_errors'``).

Fixture ``tests/fixtures/jfk_ask_not_16k.flac``: the first 7.55 s of President
Kennedy's 1961 inaugural address (US federal government work, public domain),
taken from whisper.cpp's ``samples/jfk.wav`` and re-encoded to 16 kHz mono FLAC.

Run with: pytest -m integration tests/integration/test_whisper_real.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import transcription_service

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "jfk_ask_not_16k.flac"
FIXTURE_DURATION_S = 7.55
SPOKEN = "And so my fellow Americans, ask not what your country can do for you"
REQUIRED_WORDS = frozenset({"americans", "ask", "country"})
MIN_WORD_RECALL = 0.8

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


def _normalise(word: str) -> str:
    return re.sub(r"[^a-z']", "", word.lower())


@pytest.fixture
def real_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the service to the real model and drop any model cached by another test."""
    monkeypatch.setattr(
        transcription_service.settings, "transcription_provider", "whisper"
    )
    monkeypatch.setattr(transcription_service.settings, "whisper_model", "base")
    monkeypatch.setattr(transcription_service, "_model", None)


@pytest.mark.usefixtures("real_whisper")
def test_real_base_model_transcribes_known_phrase_with_monotonic_timestamps() -> None:
    words = transcription_service.transcribe_to_words(str(FIXTURE), language="en")

    heard = [_normalise(w.word) for w in words]
    expected = [_normalise(w) for w in SPOKEN.split()]
    missing_required = REQUIRED_WORDS - set(heard)
    assert not missing_required, f"missing {sorted(missing_required)} in {heard}"
    recall = sum(word in heard for word in expected) / len(expected)
    assert recall >= MIN_WORD_RECALL, f"recall {recall:.2f}: heard {heard}"

    starts = [w.start for w in words]
    assert starts == sorted(starts), f"start times not monotonic: {starts}"
    for w in words:
        assert 0.0 <= w.start <= w.end <= FIXTURE_DURATION_S + 0.5, w
