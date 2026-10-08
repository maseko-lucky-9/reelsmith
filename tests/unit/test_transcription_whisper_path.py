"""Whisper-provider behaviour of transcription_service against a fake model.

Covers language normalisation, the locked lazy model load, decode settings
(beam size / VAD / CPU threads) and the decode-time budget: it is charged
only once decoding starts, scales with the audio length and, on timeout or
cancellation, stops the worker's segment loop instead of leaking the thread.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.services import transcription_service as ts
from tests.unit.fake_whisper import FakeModelFactory, install


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> FakeModelFactory:
    return install(monkeypatch)


def _language_sent(factory: FakeModelFactory) -> str | None:
    return factory.model.transcribe_calls[-1]["language"]


# ── Language ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("en-US", "en"),
        ("pt-BR", "pt"),
        ("en", "en"),
        ("EN_gb", "en"),
        (" de-AT ", "de"),
        ("auto", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_language(tag: str | None, expected: str | None) -> None:
    assert ts.normalize_language(tag) == expected


def test_region_tag_is_normalised_before_reaching_whisper(
    factory: FakeModelFactory,
) -> None:
    words = ts.transcribe_to_words("a.wav", language="en-US")

    assert [w.word for w in words] == ["w0", "w1"]
    assert _language_sent(factory) == "en"


def test_unspecified_language_uses_the_configured_default(
    factory: FakeModelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ts.settings, "default_transcription_language", "pt-BR")

    ts.transcribe_to_words("a.wav")

    assert _language_sent(factory) == "pt"


def test_unsupported_language_falls_back_to_the_configured_default(
    factory: FakeModelFactory,
) -> None:
    ts.transcribe_to_words("a.wav", language="xx-YY")

    assert _language_sent(factory) == "en"


def test_unsupported_language_and_default_fall_back_to_auto_detect(
    factory: FakeModelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ts.settings, "default_transcription_language", "zz")

    ts.transcribe_to_words("a.wav", language="xx")

    assert _language_sent(factory) is None


def test_auto_requests_language_detection(factory: FakeModelFactory) -> None:
    ts.transcribe_to_words("a.wav", language="auto")

    assert _language_sent(factory) is None


def test_speech_to_text_normalises_region_tag(factory: FakeModelFactory) -> None:
    assert ts.speech_to_text("a.wav", language="pt-BR") == "w0 w1"
    assert _language_sent(factory) == "pt"


# ── Model load and decode settings ───────────────────────────────────────────


def test_concurrent_first_calls_construct_the_model_once(
    factory: FakeModelFactory,
) -> None:
    factory.load_delay = 0.2
    barrier = threading.Barrier(2)
    loaded: list[object] = []

    def first_call() -> None:
        barrier.wait()
        loaded.append(ts._get_model())

    threads = [threading.Thread(target=first_call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(factory.constructed) == 1
    assert loaded == [factory.model, factory.model]


@pytest.mark.parametrize("vad_filter", [True, False])
def test_decode_settings_reach_whisper(
    factory: FakeModelFactory, monkeypatch: pytest.MonkeyPatch, vad_filter: bool
) -> None:
    monkeypatch.setattr(ts.settings, "whisper_model", "tiny")
    monkeypatch.setattr(ts.settings, "whisper_cpu_threads", 6)
    monkeypatch.setattr(ts.settings, "whisper_beam_size", 3)
    monkeypatch.setattr(ts.settings, "whisper_vad_filter", vad_filter)

    ts.transcribe_to_words("a.wav", language="en")

    assert factory.constructed == [
        (("tiny",), {"compute_type": "int8", "cpu_threads": 6})
    ]
    call = factory.model.transcribe_calls[-1]
    assert call["beam_size"] == 3
    assert call["vad_filter"] is vad_filter
    assert call["word_timestamps"] is True


def test_warm_up_loads_the_model(factory: FakeModelFactory) -> None:
    ts.warm_up()

    assert len(factory.constructed) == 1


def test_warm_up_is_a_noop_for_the_stub_provider(
    factory: FakeModelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ts.settings, "transcription_provider", "stub")

    ts.warm_up()

    assert factory.constructed == []


# ── Decode-time budget, cancellation ─────────────────────────────────────────


def test_decode_timeout_is_the_larger_of_setting_and_audio_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ts.settings, "transcription_timeout_seconds", 120)

    assert ts.decode_timeout_seconds(30.0) == 120
    assert ts.decode_timeout_seconds(900.0) == 900.0


async def test_timeout_scales_with_audio_duration(
    factory: FakeModelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory.model.segments, factory.model.delay = 6, 0.05  # ~0.3 s of decoding
    monkeypatch.setattr(ts.settings, "transcription_timeout_seconds", 0.1)

    words = await ts.transcribe_words_async(
        "a.wav", language="en", audio_duration_s=1.0
    )

    assert len(words) == 6


async def test_model_load_is_not_charged_to_the_timeout(
    factory: FakeModelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory.load_delay = 0.5
    monkeypatch.setattr(ts.settings, "transcription_timeout_seconds", 0.3)

    words = await ts.transcribe_words_async(
        "a.wav", language="en", audio_duration_s=0.0
    )

    assert len(words) == 2


async def test_waiting_for_another_decode_is_not_charged_to_the_timeout(
    factory: FakeModelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory.model.segments, factory.model.delay = 5, 0.1  # ~0.5 s per decode
    monkeypatch.setattr(ts.settings, "transcription_timeout_seconds", 0.8)

    first, second = await asyncio.gather(
        ts.transcribe_words_async("a.wav", language="en", audio_duration_s=0.0),
        ts.transcribe_words_async("b.wav", language="en", audio_duration_s=0.0),
    )

    assert len(first) == len(second) == 5


async def test_timeout_stops_the_segment_loop(
    factory: FakeModelFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory.model.segments, factory.model.delay = 60, 0.05  # ~3 s of decoding
    monkeypatch.setattr(ts.settings, "transcription_timeout_seconds", 0.2)

    t0 = time.perf_counter()
    with pytest.raises(TimeoutError, match="exceeded its 0s decode budget"):
        await ts.transcribe_words_async("a.wav", language="en", audio_duration_s=0.0)

    assert time.perf_counter() - t0 < 1.0
    assert factory.model.closed.is_set(), "segment generator still open"
    consumed = factory.model.consumed
    await asyncio.sleep(0.2)
    assert factory.model.consumed == consumed < 60


async def test_cancelling_the_caller_stops_the_segment_loop(
    factory: FakeModelFactory,
) -> None:
    factory.model.segments, factory.model.delay = 60, 0.05
    task = asyncio.create_task(
        ts.transcribe_words_async("a.wav", language="en", audio_duration_s=0.0)
    )
    while factory.model.consumed < 2:
        await asyncio.sleep(0.01)

    t0 = time.perf_counter()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert time.perf_counter() - t0 < 1.0
    assert factory.model.closed.is_set(), "segment generator still open"
    consumed = factory.model.consumed
    await asyncio.sleep(0.2)
    assert factory.model.consumed == consumed < 60


def test_cancel_event_set_before_decoding_skips_the_decode(
    factory: FakeModelFactory,
) -> None:
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(ts.TranscriptionCancelled):
        ts.transcribe_to_words("a.wav", language="en", cancel=cancel)

    assert factory.model.transcribe_calls == []


async def test_cancelling_a_call_queued_behind_another_decode_returns_promptly(
    factory: FakeModelFactory,
) -> None:
    factory.model.segments, factory.model.delay = 60, 0.05  # ~3 s per decode
    running = asyncio.create_task(
        ts.transcribe_words_async("a.wav", language="en", audio_duration_s=0.0)
    )
    while factory.model.consumed < 1:
        await asyncio.sleep(0.01)
    queued = asyncio.create_task(
        ts.transcribe_words_async("b.wav", language="en", audio_duration_s=0.0)
    )
    await asyncio.sleep(0.2)  # the queued call is now waiting for the decode slot

    t0 = time.perf_counter()
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued

    assert time.perf_counter() - t0 < 1.0
    assert len(factory.model.transcribe_calls) == 1, "queued call must not decode"
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
