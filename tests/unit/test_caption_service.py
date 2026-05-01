import pytest
import pysrt
from webvtt import WebVTT

from app.services.caption_service import (
    captions_to_dicts,
    captions_to_text,
    generate_captions,
)


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
