"""A stand-in for ``faster_whisper.WhisperModel`` so unit tests never load a model.

``FakeWhisperModel.transcribe`` mirrors the faster-whisper 1.2.1 contract the
service relies on:

* it returns ``(segments, info)`` where ``segments`` is a *lazy* generator —
  decoding happens as the caller iterates, one segment at a time;
* it raises ``ValueError("'<code>' is not a valid language code ...")`` for a
  language its tokenizer doesn't know (``faster_whisper.tokenizer.Tokenizer``),
  which is what a raw BCP-47 tag such as ``"en-US"`` hits. The real-library
  rejection is asserted in ``tests/integration/test_whisper_real.py``.

``FakeModelFactory`` replaces the ``WhisperModel`` class: it records every
construction (args/kwargs) and can be slowed down to simulate a model load.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import faster_whisper
import pytest

SUPPORTED_LANGUAGES = ("en", "pt", "de", "fr")


@dataclass(frozen=True, slots=True)
class FakeWord:
    word: str
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class FakeSegment:
    words: tuple[FakeWord, ...]


class FakeWhisperModel:
    """Yields ``segments`` one-word segments, sleeping ``delay`` s before each."""

    supported_languages = list(SUPPORTED_LANGUAGES)

    def __init__(self, *, segments: int = 2, delay: float = 0.0) -> None:
        self.segments = segments
        self.delay = delay
        self.transcribe_calls: list[dict[str, Any]] = []
        self.consumed = 0
        self.closed = threading.Event()

    def transcribe(
        self, audio: str, **kwargs: Any
    ) -> tuple[Iterator[FakeSegment], object]:
        self.transcribe_calls.append(kwargs)
        language = kwargs.get("language")
        if language is not None and language not in self.supported_languages:
            raise ValueError(f"'{language}' is not a valid language code")
        return self._generate(), object()

    def _generate(self) -> Iterator[FakeSegment]:
        try:
            for i in range(self.segments):
                time.sleep(self.delay)
                self.consumed += 1
                yield FakeSegment((FakeWord(f" w{i}", i * 0.5, i * 0.5 + 0.4),))
        finally:
            self.closed.set()


@dataclass
class FakeModelFactory:
    model: FakeWhisperModel = field(default_factory=FakeWhisperModel)
    load_delay: float = 0.0
    constructed: list[tuple[tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list
    )

    def __call__(self, *args: Any, **kwargs: Any) -> FakeWhisperModel:
        time.sleep(self.load_delay)
        self.constructed.append((args, kwargs))
        return self.model


def install(monkeypatch: pytest.MonkeyPatch) -> FakeModelFactory:
    """Route ``transcription_service`` to the whisper provider backed by a fake."""
    from app.services import transcription_service

    factory = FakeModelFactory()
    monkeypatch.setattr(faster_whisper, "WhisperModel", factory)
    monkeypatch.setattr(transcription_service, "_model", None)
    settings = transcription_service.settings
    monkeypatch.setattr(settings, "transcription_provider", "whisper")
    monkeypatch.setattr(settings, "default_transcription_language", "en-US")
    return factory
