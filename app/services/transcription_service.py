from __future__ import annotations

import logging
from dataclasses import dataclass

from app.settings import settings

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)

_model = None


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        log.info("Loading Whisper model  name=%s", settings.whisper_model)
        _model = WhisperModel(settings.whisper_model, compute_type="int8")
        log.info("Whisper model loaded")
    return _model


_STUB_WORDS = [
    WordTiming("stub", 0.0, 0.5),
    WordTiming("transcription", 0.5, 1.2),
    WordTiming("text", 1.2, 1.6),
    WordTiming("for", 1.6, 1.8),
    WordTiming("testing", 1.8, 2.4),
]


def transcribe_to_words(audio_path: str, language: str = "en") -> list[WordTiming]:
    if settings.transcription_provider == "stub":
        log.info("Stub transcription provider; returning placeholder words")
        return list(_STUB_WORDS)

    log.info("Transcribing audio  path=%s  language=%s  model=%s",
             audio_path, language, settings.whisper_model)
    model = _get_model()
    segments, _ = model.transcribe(audio_path, word_timestamps=True, language=language)
    words: list[WordTiming] = []
    for segment in segments:
        if segment.words:
            for w in segment.words:
                words.append(WordTiming(word=w.word.strip(), start=w.start, end=w.end))
    log.info("Transcription complete  words=%d", len(words))
    return words


def speech_to_text(audio_path: str, language: str = "en-US") -> str:
    """Backwards-compatible wrapper — returns plain transcript string."""
    words = transcribe_to_words(audio_path, language=language.split("-")[0])
    return " ".join(w.word for w in words)
