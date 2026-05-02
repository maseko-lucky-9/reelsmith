import pytest
import pysrt
from webvtt import WebVTT

from app.services.caption_service import (
    captions_to_dicts,
    captions_to_text,
    generate_captions,
    generate_captions_from_word_timings,
)
from app.services.transcription_service import WordTiming


def _words(n: int = 6) -> list[WordTiming]:
    return [WordTiming(word=f"w{i}", start=float(i), end=float(i) + 0.9) for i in range(n)]


def _sample_text():
    return "the quick brown fox jumps over the lazy dog and then runs away fast"


def test_generate_srt_returns_subripfile_with_chunks():
    captions = generate_captions(_sample_text(), 0, 10, format="srt")
    assert isinstance(captions, pysrt.SubRipFile)
    items = list(captions)
    assert len(items) >= 2
    assert items[0].start.ordinal == 0


def test_generate_vtt_returns_webvtt_with_chunks():
    captions = generate_captions(_sample_text(), 0, 10, format="vtt")
    assert isinstance(captions, WebVTT)
    items = list(captions)
    assert len(items) >= 2


def test_empty_text_returns_empty_container():
    srt = generate_captions("", 0, 5, format="srt")
    vtt = generate_captions("", 0, 5, format="vtt")
    assert len(list(srt)) == 0
    assert len(list(vtt)) == 0


def test_unsupported_format_raises():
    with pytest.raises(ValueError):
        generate_captions("hello world", 0, 1, format="bogus")


def test_captions_to_dicts_srt_contains_index_and_times():
    captions = generate_captions(_sample_text(), 0, 10, format="srt")
    dicts = captions_to_dicts(captions, "srt")
    assert dicts[0]["index"] == 1
    assert "text" in dicts[0]
    assert dicts[0]["start"] == 0.0


def test_captions_to_text_srt_round_trips():
    captions = generate_captions(_sample_text(), 0, 10, format="srt")
    body = captions_to_text(captions, "srt")
    assert "1" in body
    assert "fox" in body


# ── generate_captions_from_word_timings ──────────────────────────────────────

def test_word_timing_groups_of_n():
    captions = generate_captions_from_word_timings(_words(6), n=3, format="srt")
    items = list(captions)
    assert len(items) == 2
    assert items[0].text == "w0 w1 w2"
    assert items[1].text == "w3 w4 w5"


def test_word_timing_remainder_group():
    captions = generate_captions_from_word_timings(_words(7), n=3, format="srt")
    items = list(captions)
    assert len(items) == 3
    assert items[2].text == "w6"


def test_word_timing_timestamps_from_words():
    words = _words(3)
    captions = generate_captions_from_word_timings(words, n=3, format="srt")
    item = list(captions)[0]
    assert item.start.ordinal / 1000.0 == pytest.approx(words[0].start)
    assert item.end.ordinal / 1000.0 == pytest.approx(words[-1].end, abs=0.01)


def test_word_timing_vtt_format():
    captions = generate_captions_from_word_timings(_words(4), n=2, format="vtt")
    items = list(captions)
    assert len(items) == 2


def test_word_timing_single_word():
    captions = generate_captions_from_word_timings([WordTiming("only", 1.0, 1.5)], n=3)
    assert len(list(captions)) == 1


def test_word_timing_empty_input_srt():
    captions = generate_captions_from_word_timings([], n=3, format="srt")
    assert len(list(captions)) == 0


def test_word_timing_empty_input_vtt():
    captions = generate_captions_from_word_timings([], n=3, format="vtt")
    assert len(list(captions)) == 0


def test_word_timing_unsupported_format():
    with pytest.raises(ValueError):
        generate_captions_from_word_timings(_words(3), n=3, format="bogus")
