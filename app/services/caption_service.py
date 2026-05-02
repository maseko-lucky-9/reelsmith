import logging
from pathlib import Path

import pysrt
from webvtt import Caption, WebVTT

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


def generate_captions_from_word_timings(words: list, n: int = 3, format: str = "srt"):
    """Generate captions from word-level timestamps, grouping N words per segment.

    Each element of *words* must have .word (str), .start (float), .end (float).
    """
    if not words:
        log.warning("No word timings — returning empty captions")
        return pysrt.SubRipFile() if format == "srt" else WebVTT()

    captions = []
    for i in range(0, len(words), n):
        group = words[i:i + n]
        text = " ".join(w.word for w in group)
        start = group[0].start
        end = group[-1].end

        if format == "srt":
            caption = pysrt.SubRipItem(
                index=len(captions) + 1,
                start=pysrt.SubRipTime(seconds=start),
                end=pysrt.SubRipTime(seconds=end),
                text=text,
            )
        elif format == "vtt":
            caption = Caption(
                start=_format_vtt_time(start),
                end=_format_vtt_time(end),
                text=text,
            )
        else:
            raise ValueError(f"Unsupported format: {format}")
        captions.append(caption)

    log.info("Captions generated from word timings  count=%d  format=%s", len(captions), format)
    if format == "srt":
        return pysrt.SubRipFile(items=captions)
    vtt = WebVTT()
    for c in captions:
        vtt.captions.append(c)
    return vtt


# Deprecated: uses uniform words-per-second rate, not actual word timestamps.
def generate_captions(text: str, start_time: float, end_time: float, format: str = "srt"):
    duration = end_time - start_time
    words = text.split()
    log.info("Generating captions  format=%s  words=%d  duration=%.1fs", format, len(words), duration)
    if not words or duration <= 0:
        log.warning("Empty text or zero duration — returning empty captions")
        return pysrt.SubRipFile() if format == "srt" else WebVTT()

    words_per_second = len(words) / duration
    captions = []
    current_time = start_time
    caption_text = ""

    for word in words:
        caption_text += word + " "
        if len(caption_text.split()) >= 7 or word == words[-1]:
            end_caption_time = current_time + len(caption_text.split()) / words_per_second
            if format == "srt":
                caption = pysrt.SubRipItem(
                    index=len(captions) + 1,
                    start=pysrt.SubRipTime(seconds=current_time),
                    end=pysrt.SubRipTime(seconds=end_caption_time),
                    text=caption_text.strip(),
                )
            elif format == "vtt":
                caption = Caption(
                    start=_format_vtt_time(current_time),
                    end=_format_vtt_time(end_caption_time),
                    text=caption_text.strip(),
                )
            else:
                raise ValueError(f"Unsupported format: {format}")
            captions.append(caption)
            current_time = end_caption_time
            caption_text = ""

    log.info("Captions generated  count=%d  format=%s", len(captions), format)
    if format == "srt":
        return pysrt.SubRipFile(items=captions)

    vtt = WebVTT()
    for caption in captions:
        vtt.captions.append(caption)
    return vtt


def _format_vtt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def write_captions(captions, format: str, path: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if format == "srt":
        captions.save(path, encoding="utf-8")
    elif format == "vtt":
        captions.save(path)
    else:
        raise ValueError(f"Unsupported format: {format}")
    log.debug("Captions written  path=%s", path)
    return path


def captions_to_dicts(captions, format: str) -> list[dict]:
    out = []
    if format == "srt":
        for index, item in enumerate(captions, start=1):
            out.append(
                {
                    "index": index,
                    "start": item.start.ordinal / 1000.0,
                    "end": item.end.ordinal / 1000.0,
                    "text": item.text,
                }
            )
    elif format == "vtt":
        for index, caption in enumerate(captions, start=1):
            out.append(
                {
                    "index": index,
                    "start": caption.start_in_seconds,
                    "end": caption.end_in_seconds,
                    "text": caption.text,
                }
            )
    else:
        raise ValueError(f"Unsupported format: {format}")
    return out


def captions_to_text(captions, format: str) -> str:
    if format == "srt":
        return "\n".join(str(item) for item in captions)
    if format == "vtt":
        return captions.content
    raise ValueError(f"Unsupported format: {format}")
