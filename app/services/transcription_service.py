"""Speech to word-timed text: faster-whisper, or a deterministic stub.

Provider (``YTVIDEO_TRANSCRIPTION_PROVIDER``): ``whisper`` or ``stub``.

* **Model** — one process-wide ``WhisperModel`` (``whisper_model``, int8,
  ``whisper_cpu_threads``), loaded lazily under a lock so concurrent first
  calls build it once; the API lifespan pre-loads it via ``warm_up()``.
* **Language** — job languages are BCP-47 tags (``"en-US"``), which
  faster-whisper rejects with ``ValueError``; ``resolve_language`` maps them to
  the ISO 639-1 code Whisper knows (see its docstring for the fallbacks).
* **Decoding** — one decode at a time (CTranslate2 runs one per model with
  ``num_workers=1`` anyway); ``whisper_beam_size`` and ``whisper_vad_filter``
  come from settings. Segments are a lazy generator, so the cancel event is
  checked between segments and a cancelled call stops decoding promptly.
* **Budget** — ``transcribe_words_async`` charges
  ``max(transcription_timeout_seconds, audio seconds)`` from the moment
  decoding starts, not while the call waits for a thread, the model load or
  another chapter's decode.
* **Repeatable decoding** (``repeatable=True``: clip discovery, T047) — by
  default faster-whisper re-decodes a 30 s window by *sampling* at
  temperatures 0.2-1.0 when its greedy text compresses too well or scores too
  low, so one source can give different words run to run (a TEDx talk gave
  1006, 1008 and 1002 words and two different kept-clip sets). A repeatable
  call decodes greedily (``temperature=0.0`` only): the same audio gives the
  same words on one install. Greedy decoding alone can loop on noisy audio
  (one phrase copied window after window), so when the greedy result has
  such a loop (``has_decode_loop``) the audio is decoded again with the
  default fallback and that result, which is not repeatable, is used.
  Measurements: ADR-007 *Repeatable transcription*.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import app.logging_config  # noqa: F401
from app.services import ffmpeg_tools
from app.settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from faster_whisper import WhisperModel

log = logging.getLogger(__name__)

AUTO_DETECT = "auto"
# How often a call queued behind another decode re-checks its cancel event.
_LOCK_POLL_SECONDS = 0.1

# Repeatable decoding (T047): one temperature, so no sampled fallback.
GREEDY_TEMPERATURE = 0.0
# A decode loop: one phrase of up to LOOP_MAX_PHRASE_WORDS words, at least
# LOOP_MIN_COPIES times back to back, over at least LOOP_MIN_WORDS words (a
# third of a 30 s window of speech). Greedy loops measured on noisy talks
# covered 168-549 words; the longest repeat in a greedy decode of a clean
# talk was a 15-word stutter ("I don't know." x5). A false alarm only costs
# repeatability: the fallback result is used.
LOOP_MIN_COPIES = 3
LOOP_MIN_WORDS = 25
LOOP_MAX_PHRASE_WORDS = 20

_model: WhisperModel | None = None
_model_lock = threading.Lock()
_decode_lock = threading.Lock()


class TranscriptionCancelled(RuntimeError):
    """The call's cancel event was set; decoding stopped early."""


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


_STUB_WORDS = [
    WordTiming("stub", 0.0, 0.5),
    WordTiming("transcription", 0.5, 1.2),
    WordTiming("text", 1.2, 1.6),
    WordTiming("for", 1.6, 1.8),
    WordTiming("testing", 1.8, 2.4),
]


# ── Language ──────────────────────────────────────────────────────────────────


def normalize_language(tag: str | None) -> str | None:
    """Primary subtag of a language tag, lower-cased: ``"en-US"`` → ``"en"``.

    ``None``, blank or ``"auto"`` → ``None`` (let Whisper detect the language).
    """
    if tag is None:
        return None
    primary = tag.strip().replace("_", "-").split("-")[0].lower()
    return None if primary in ("", AUTO_DETECT) else primary


def resolve_language(requested: str | None, supported: Collection[str]) -> str | None:
    """The language code to pass Whisper, or ``None`` to auto-detect.

    ``requested`` ``None`` means "not specified" and uses
    ``settings.default_transcription_language``; ``"auto"`` or blank asks for
    detection. A code the model doesn't support falls back to the configured
    default, and to detection when the default isn't supported either.
    """
    if requested is None:
        requested = settings.default_transcription_language
    code = normalize_language(requested)
    if code is None or code in supported:
        return code
    fallback = normalize_language(settings.default_transcription_language)
    if fallback is not None and fallback not in supported:
        fallback = None
    log.warning(
        "Language %r is not supported by the Whisper model; using %s",
        requested,
        fallback or "auto-detection",
    )
    return fallback


# ── Model ─────────────────────────────────────────────────────────────────────


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel

                log.info(
                    "Loading Whisper model  name=%s  cpu_threads=%d",
                    settings.whisper_model,
                    settings.whisper_cpu_threads,
                )
                _model = WhisperModel(
                    settings.whisper_model,
                    compute_type="int8",
                    cpu_threads=settings.whisper_cpu_threads,
                )
                log.info("Whisper model loaded")
    return _model


def warm_up() -> None:
    """Load the Whisper model now (no-op for the stub provider)."""
    if settings.transcription_provider == "whisper":
        _get_model()


# ── Transcription ─────────────────────────────────────────────────────────────


def _raise_if_cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise TranscriptionCancelled("transcription cancelled")


@contextmanager
def _decode_slot(cancel: threading.Event | None) -> Iterator[None]:
    """Hold the decode lock; give up waiting as soon as ``cancel`` is set."""
    while not _decode_lock.acquire(timeout=_LOCK_POLL_SECONDS):
        _raise_if_cancelled(cancel)
    try:
        _raise_if_cancelled(cancel)
        yield
    finally:
        _decode_lock.release()


def _phrase_token(word: str) -> str:
    return "".join(ch for ch in word.casefold() if ch.isalnum())


def has_decode_loop(words: Sequence[str]) -> bool:
    """Whether one phrase repeats back to back long enough to be a decode loop.

    That is a phrase of 1 to ``LOOP_MAX_PHRASE_WORDS`` words, at least
    ``LOOP_MIN_COPIES`` times in a row, over at least ``LOOP_MIN_WORDS``
    words. Case and punctuation are ignored (``"Yeah."`` matches
    ``"yeah,"``). Sentences that only share an opening ("This is him ...
    This is him ...") do not count.
    """
    tokens = [t for t in map(_phrase_token, words) if t]
    for size in range(1, LOOP_MAX_PHRASE_WORDS + 1):
        # ``run`` tokens in a row, each equal to the token ``size`` places on,
        # make a stretch of ``run + size`` words: (run + size) / size copies.
        run = 0
        for i in range(len(tokens) - size):
            run = run + 1 if tokens[i] == tokens[i + size] else 0
            if run >= size * (LOOP_MIN_COPIES - 1) and run + size >= LOOP_MIN_WORDS:
                return True
    return False


def _decode(
    model: WhisperModel,
    audio_path: str,
    code: str | None,
    cancel: threading.Event | None,
    **decoding: float,
) -> list[WordTiming]:
    """One faster-whisper pass; ``decoding`` adds options (``temperature``)."""
    log.info(
        "Transcribing audio  path=%s  language=%s  model=%s  beam=%d  vad=%s  decoding=%s",
        audio_path,
        code or AUTO_DETECT,
        settings.whisper_model,
        settings.whisper_beam_size,
        settings.whisper_vad_filter,
        "greedy" if decoding else "temperature fallback",
    )
    segments, _info = model.transcribe(
        audio_path,
        word_timestamps=True,
        language=code,
        beam_size=settings.whisper_beam_size,
        vad_filter=settings.whisper_vad_filter,
        **decoding,
    )
    words: list[WordTiming] = []
    with closing(segments):
        for segment in segments:
            words.extend(
                WordTiming(word=w.word.strip(), start=w.start, end=w.end)
                for w in segment.words or ()
            )
            _raise_if_cancelled(cancel)
    return words


def transcribe_to_words(
    audio_path: str,
    language: str | None = None,
    *,
    repeatable: bool = False,
    cancel: threading.Event | None = None,
    on_start: Callable[[], None] | None = None,
) -> list[WordTiming]:
    """Word-timed transcript of ``audio_path`` (blocking; run it in a thread).

    ``language`` is a job language tag, resolved by ``resolve_language``.
    ``repeatable`` decodes greedily, so the same audio gives the same words,
    unless the greedy result loops (``has_decode_loop``): then the audio is
    decoded again with faster-whisper's default temperature fallback (module
    docstring). ``cancel`` defaults to the event
    ``ffmpeg_tools.to_thread_cancellable`` installs; once set, the call raises
    ``TranscriptionCancelled`` at the next segment boundary. ``on_start`` is
    called when decoding begins (model loaded, decode slot held), so a caller
    can start its timeout clock there; a second pass runs on that clock too.

    Raises:
        TranscriptionCancelled: ``cancel`` was set before decoding finished.
    """
    if settings.transcription_provider == "stub":
        log.info("Stub transcription provider; returning placeholder words")
        if on_start is not None:
            on_start()
        return list(_STUB_WORDS)

    if cancel is None:
        cancel = ffmpeg_tools.current_cancel_event()
    model = _get_model()
    code = resolve_language(language, model.supported_languages)
    with _decode_slot(cancel):
        if on_start is not None:
            on_start()
        if repeatable:
            words = _decode(model, audio_path, code, cancel, temperature=GREEDY_TEMPERATURE)
            if has_decode_loop([w.word for w in words]):
                log.warning(
                    "Greedy transcription repeats a phrase back to back (a decode loop); decoding "
                    "again with the temperature fallback (not repeatable)  path=%s",
                    audio_path,
                )
                words = _decode(model, audio_path, code, cancel)
        else:
            words = _decode(model, audio_path, code, cancel)
    log.info("Transcription complete  words=%d", len(words))
    return words


def speech_to_text(audio_path: str, language: str | None = "en-US") -> str:
    """Backwards-compatible wrapper — returns the plain transcript string."""
    words = transcribe_to_words(audio_path, language=language)
    return " ".join(w.word for w in words)


def decode_timeout_seconds(audio_duration_s: float) -> float:
    """Decode budget for ``audio_duration_s`` of audio: at least real time."""
    return max(settings.transcription_timeout_seconds, audio_duration_s)


async def transcribe_words_async(
    audio_path: str,
    *,
    language: str | None,
    audio_duration_s: float,
    repeatable: bool = False,
) -> list[WordTiming]:
    """``transcribe_to_words`` in a worker thread under a decode-time budget.

    The budget (``decode_timeout_seconds``) starts when decoding does. On
    timeout or cancellation the worker's cancel event is set and this waits
    for the worker to stop at its next segment boundary, so no thread is left
    decoding.

    Raises:
        TimeoutError: decoding exceeded its budget.
    """
    loop = asyncio.get_running_loop()
    budget = decode_timeout_seconds(audio_duration_s)
    try:
        async with asyncio.timeout(None) as deadline:
            armed = True

            def arm() -> None:
                if armed:
                    deadline.reschedule(loop.time() + budget)

            try:
                return await ffmpeg_tools.to_thread_cancellable(
                    transcribe_to_words,
                    audio_path,
                    language=language,
                    repeatable=repeatable,
                    on_start=lambda: loop.call_soon_threadsafe(arm),
                )
            finally:
                armed = False
    except TimeoutError as exc:
        raise TimeoutError(
            f"transcription exceeded its {budget:.0f}s decode budget"
        ) from exc
