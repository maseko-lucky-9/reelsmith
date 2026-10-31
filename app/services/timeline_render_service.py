"""Editor-driven timeline render (W1.12; schema v2, spec 016 T127).

Consumes the ``ClipEdit.timeline`` JSON produced by the inline editor
(W1.2 router) and produces a deterministic, validated render plan.

* ``build_render_plan(timeline, base_clip_path, ...)`` — pure function
  (bar the audio file lookup) that validates the timeline and emits a
  normalised ``RenderPlan``. The one-pass ffmpeg render consumes it.
* ``validate_timeline(timeline, ...)`` — the same checks, run by
  ``PUT /clips/{id}/edit`` before a timeline is stored.

(The MoviePy-based ``render_with_moviepy`` writer was removed in perf P1: it
had no callers and needed ImageMagick for ``TextClip``.)

Two schema versions, told apart by the top-level ``schema_version`` (absent
means 1). Track layout (both):

    {
      "schema_version": 2,          # v2 only
      "caption_style": "hormozi",   # optional preset key (PRESETS) or null
      "mute_source": false,         # optional
      "tracks": [
        { "kind": "video",        "items": [...] },
        { "kind": "caption",      "items": [...] },
        { "kind": "text-overlay", "items": [...] },
        { "kind": "audio",        "items": [...] }   # v2 only
      ]
    }

v1 items (unchanged; times on the reel's clock):

    video:        { start, end, src ('main' or absolute path), trim_start? }
    caption:      { start, end, text?, style?, words? }
    text-overlay: { start, end, text, x, y, font_size?, color? }

v2 items:

    video:        { start, end, src? ('main') } — one SEGMENT of the clip's
                  source video, times in SOURCE seconds. Segments play in
                  order, back to back (split / trim / extend); they must be
                  in source order, must not overlap, and must lie inside the
                  source (``source_range``). The edited reel's clock is the
                  concatenation of the segments.
    caption:      { start, end, text, words[] } — on the edited reel's clock
    text-overlay: as v1, on the edited reel's clock
    audio:        { start, end, src, gain? } — voice-over: ``src`` is a file
                  NAME resolved inside the clip's own folder (the server
                  decides the folder; ``resolve_audio_src``), ``gain`` in
                  [0, ``MAX_AUDIO_GAIN``]

Caption words: when the caption's text has as many words as ``words``, each
word keeps its timing (clamped to the caption) and takes the edited spelling;
otherwise the text's words are spread evenly across the caption.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any

from app.domain.events import EventType, emit_from_sync
from app.services.animated_caption_service import PRESETS

if TYPE_CHECKING:  # pragma: no cover - import only for typing
    from app.bus.event_bus import AsyncEventBus

log = logging.getLogger(__name__)

TIMELINE_SCHEMA_VERSION = 2
"""The current timeline schema; a timeline without ``schema_version`` is v1."""

_KINDS_BY_VERSION: dict[int, frozenset[str]] = {
    1: frozenset({"video", "caption", "text-overlay"}),
    2: frozenset({"video", "caption", "text-overlay", "audio"}),
}

MAX_TRACKS = 16
MAX_ITEMS: dict[str, int] = {"video": 100, "caption": 2000, "text-overlay": 100, "audio": 16}
"""Most items of one kind across all its tracks."""
MAX_OUTPUT_SECONDS = 20 * 60.0
"""Longest edited reel (v2: the sum of the video segments)."""
MIN_SEGMENT_SECONDS = 0.05
MAX_TEXT_CHARS = 500
MAX_CAPTION_WORDS = 100
MAX_STYLE_CHARS = 64
MAX_COLOR_CHARS = 32
FONT_SIZE_RANGE = (6, 400)
MAX_AUDIO_GAIN = 4.0
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac"})
TIME_TOLERANCE = 1e-3
"""Float slack on range checks (a segment ending at the source's end)."""


class TimelineError(ValueError):
    pass


@dataclass(frozen=True)
class VideoItem:
    """One piece of the edited reel: ``[start, end)`` on the reel's clock,
    read from ``src`` starting at ``trim_start``."""

    start: float
    end: float
    src: str
    trim_start: float = 0.0


@dataclass(frozen=True)
class SourceSegment:
    """A v2 video segment, in source seconds."""

    start: float
    end: float


@dataclass(frozen=True)
class CaptionWord:
    word: str
    start: float
    end: float


@dataclass(frozen=True)
class CaptionItem:
    start: float
    end: float
    text: str
    style: str = "default"
    words: tuple[CaptionWord, ...] = ()


@dataclass(frozen=True)
class TextOverlayItem:
    start: float
    end: float
    text: str
    x: float
    y: float
    font_size: int = 36
    color: str = "#ffffff"


@dataclass(frozen=True)
class AudioItem:
    """A voice-over file mixed in at ``[start, end)`` on the reel's clock.
    ``src`` is the file name the client sent, ``path`` where it resolved."""

    start: float
    end: float
    src: str
    path: str
    gain: float = 1.0


@dataclass(frozen=True)
class RenderPlan:
    duration: float
    video: list[VideoItem] = field(default_factory=list)
    captions: list[CaptionItem] = field(default_factory=list)
    overlays: list[TextOverlayItem] = field(default_factory=list)
    schema_version: int = 1
    segments: list[SourceSegment] = field(default_factory=list)
    """v2: the source windows, in play order (v1: empty)."""
    audio: list[AudioItem] = field(default_factory=list)
    caption_style: str | None = None
    mute_source: bool = False
    source_path: str | None = None
    """v2: the source video the segments read (None when unknown)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": self.duration,
            "video": [v.__dict__ for v in self.video],
            "captions": [
                {**{k: v for k, v in c.__dict__.items() if k != "words"}, "words": [w.__dict__ for w in c.words]}
                for c in self.captions
            ],
            "overlays": [o.__dict__ for o in self.overlays],
            "schema_version": self.schema_version,
            "segments": [s.__dict__ for s in self.segments],
            # The resolved path stays server-side.
            "audio": [{"start": a.start, "end": a.end, "src": a.src, "gain": a.gain} for a in self.audio],
            "caption_style": self.caption_style,
            "mute_source": self.mute_source,
        }


def _f(value: Any, *, name: str) -> float:
    """A finite number (numeric strings allowed, as in v1); never a bool."""
    if isinstance(value, bool):
        raise TimelineError(f"{name}: expected number, got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TimelineError(f"{name}: expected number, got {value!r}") from exc
    if not math.isfinite(number):
        raise TimelineError(f"{name}: must be a finite number, got {value!r}")
    return number


def _times(it: dict[str, Any], *, name: str, allow_zero: bool) -> tuple[float, float]:
    start = _f(it.get("start", 0), name=f"{name}.start")
    end = _f(it.get("end", 0), name=f"{name}.end")
    if start < 0 or end < 0:
        raise TimelineError(f"{name}: times must not be negative (start {start}, end {end})")
    if end < start or (end == start and not allow_zero):
        raise TimelineError(f"{name}: end ({end}) must be > start ({start})")
    return start, end


def _text(value: Any, *, name: str) -> str:
    text = "" if value is None else str(value)
    if len(text) > MAX_TEXT_CHARS:
        raise TimelineError(f"{name}: text longer than {MAX_TEXT_CHARS} characters")
    if "\x00" in text:
        raise TimelineError(f"{name}: text contains a NUL byte")
    return text


def _short_str(value: Any, *, name: str, limit: int) -> str:
    text = str(value)
    if len(text) > limit or "\x00" in text:
        raise TimelineError(f"{name}: expected a short string, got {value!r:.80}")
    return text


def _caption_words(text: str, raw: Any, start: float, end: float, *, name: str) -> tuple[CaptionWord, ...]:
    """The caption's words with timings (module docstring)."""
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise TimelineError(f"{name}.words must be a list")
    if len(raw) > MAX_CAPTION_WORDS:
        raise TimelineError(f"{name}.words: more than {MAX_CAPTION_WORDS} words")
    timings: list[tuple[float, float]] = []
    for idx, w in enumerate(raw):
        if not isinstance(w, dict):
            raise TimelineError(f"{name}.words[{idx}] must be an object")
        ws = _f(w.get("start", start), name=f"{name}.words[{idx}].start")
        we = _f(w.get("end", ws), name=f"{name}.words[{idx}].end")
        timings.append((ws, we))
    tokens = text.split()
    if not tokens:
        return ()
    if len(tokens) == len(timings):
        out = []
        for tok, (ws, we) in zip(tokens, timings):
            s = min(max(ws, start), end)
            out.append(CaptionWord(tok, s, min(max(we, s), end)))
        return tuple(out)
    # ponytail: the word count changed, so the old timings no longer map to
    # words; spreading them evenly over the caption ignores speech rhythm
    # (long and short words get the same slot). Re-aligning against the audio
    # would need a forced-alignment pass.
    step = (end - start) / len(tokens)
    return tuple(CaptionWord(tok, start + i * step, start + (i + 1) * step) for i, tok in enumerate(tokens))


def resolve_audio_src(clip_folder: str | Path | None, src: Any) -> Path:
    """The voice-over file ``src`` (a bare file name) inside ``clip_folder``.

    Refused: no folder, a non-string or empty name, a NUL byte, a path
    separator, ``.``/``..``, an absolute path, an extension outside
    ``AUDIO_EXTENSIONS``, a missing or non-regular file, and a name that
    resolves outside the folder (a symlink escaping it).
    """
    if clip_folder is None:
        raise TimelineError("audio: the clip has no folder for voice-over files")
    if not isinstance(src, str) or not src:
        raise TimelineError(f"audio src: expected a file name, got {src!r:.80}")
    # A separator anywhere also refuses every absolute path.
    if "\x00" in src or "/" in src or "\\" in src or src in {".", ".."} or len(src) > 255:
        raise TimelineError(f"audio src {src!r:.80}: must be a plain file name in the clip's folder")
    if PurePath(src).suffix.lower() not in AUDIO_EXTENSIONS:
        raise TimelineError(f"audio src {src!r}: unsupported type; expected one of {sorted(AUDIO_EXTENSIONS)}")
    folder = Path(clip_folder)
    try:
        real_folder = folder.resolve(strict=True)
        path = (folder / src).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise TimelineError(f"audio src {src!r}: no such file in the clip's folder") from exc
    if path.parent != real_folder:
        raise TimelineError(f"audio src {src!r}: resolves outside the clip's folder")
    if not path.is_file():
        raise TimelineError(f"audio src {src!r}: not a regular file")
    return path


def _schema_version(timeline: dict[str, Any]) -> int:
    raw = timeline.get("schema_version", 1)
    if raw is None:
        return 1
    if isinstance(raw, bool) or not isinstance(raw, int) or raw not in _KINDS_BY_VERSION:
        raise TimelineError(f"unsupported schema_version {raw!r}; expected one of {sorted(_KINDS_BY_VERSION)}")
    return raw


def _caption_style(timeline: dict[str, Any]) -> str | None:
    raw = timeline.get("caption_style")
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in PRESETS:
        raise TimelineError(f"unknown caption_style {raw!r:.80}; expected one of {sorted(PRESETS)} or null")
    return raw


def _mute_source(timeline: dict[str, Any]) -> bool:
    raw = timeline.get("mute_source", False)
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise TimelineError(f"mute_source: expected true or false, got {raw!r:.80}")
    return raw


def _parse(
    timeline: dict[str, Any],
    base_clip_path: str,
    *,
    source_path: str | None,
    source_range: tuple[float, float] | None,
    clip_folder: str | Path | None,
    lenient_v1: bool,
) -> RenderPlan:
    if not isinstance(timeline, dict) or "tracks" not in timeline:
        raise TimelineError("timeline missing 'tracks' key")
    tracks = timeline.get("tracks") or []
    if not isinstance(tracks, list):
        raise TimelineError("timeline.tracks must be a list")
    if len(tracks) > MAX_TRACKS:
        raise TimelineError(f"timeline has more than {MAX_TRACKS} tracks")
    version = _schema_version(timeline)
    kinds = _KINDS_BY_VERSION[version]
    caption_style = _caption_style(timeline)
    mute_source = _mute_source(timeline)

    grouped: dict[str, list[dict[str, Any]]] = {k: [] for k in kinds}
    for tr in tracks:
        kind = tr.get("kind") if isinstance(tr, dict) else None
        items = tr.get("items") if isinstance(tr, dict) else None
        if kind not in kinds:
            raise TimelineError(f"unknown track kind: {kind!r}")
        if not isinstance(items, list):
            raise TimelineError(f"track {kind!r} items must be a list")
        for it in items:
            if not isinstance(it, dict):
                raise TimelineError(f"{kind} item must be an object, got {type(it).__name__}")
        grouped[kind].extend(items)
        if len(grouped[kind]) > MAX_ITEMS[kind]:
            raise TimelineError(f"too many {kind} items (most {MAX_ITEMS[kind]})")

    # A v1 timeline being saved may hold zero-length placeholders (the web
    # editor's initial video item); planning one still refuses them.
    allow_zero = version == 1 and lenient_v1

    video: list[VideoItem] = []
    segments: list[SourceSegment] = []
    if version == 1:
        for idx, it in enumerate(grouped["video"]):
            start, end = _times(it, name=f"video[{idx}]", allow_zero=allow_zero)
            trim_start = _f(it.get("trim_start", 0), name="video.trim_start")
            if trim_start < 0:
                raise TimelineError(f"video[{idx}].trim_start must not be negative")
            src = it.get("src") or "main"
            video.append(
                VideoItem(
                    start=start,
                    end=end,
                    src=base_clip_path if src == "main" else str(src),
                    trim_start=trim_start,
                )
            )
    else:
        segments = _segments(grouped["video"], source_range)
        cursor = 0.0
        for seg in segments:
            length = seg.end - seg.start
            video.append(VideoItem(start=cursor, end=cursor + length, src=source_path or "main", trim_start=seg.start))
            cursor += length

    # v2 items live on the edited reel's clock, which the segments define.
    reel_end = video[-1].end if version == 2 else None

    def _placed(it: dict[str, Any], name: str) -> tuple[float, float]:
        start, end = _times(it, name=name, allow_zero=allow_zero)
        if reel_end is not None and end > reel_end + TIME_TOLERANCE:
            raise TimelineError(f"{name}: ends at {end}, past the edited reel's end ({reel_end:.3f})")
        return start, end

    captions: list[CaptionItem] = []
    for idx, it in enumerate(grouped["caption"]):
        name = f"captions[{idx}]"
        start, end = _placed(it, name)
        text = _text(it.get("text", ""), name=name)
        captions.append(
            CaptionItem(
                start=start,
                end=end,
                text=text,
                style=_short_str(it.get("style", "default"), name=f"{name}.style", limit=MAX_STYLE_CHARS),
                words=_caption_words(text, it.get("words"), start, end, name=name),
            )
        )

    overlays: list[TextOverlayItem] = []
    for idx, it in enumerate(grouped["text-overlay"]):
        name = f"overlays[{idx}]"
        start, end = _placed(it, name)
        font_size = int(_f(it.get("font_size", 36), name=f"{name}.font_size"))
        lo, hi = FONT_SIZE_RANGE
        if not lo <= font_size <= hi:
            raise TimelineError(f"{name}.font_size must be in [{lo}, {hi}], got {font_size}")
        overlays.append(
            TextOverlayItem(
                start=start,
                end=end,
                text=_text(it.get("text", ""), name=name),
                x=_f(it.get("x", 0.5), name="overlay.x"),
                y=_f(it.get("y", 0.1), name="overlay.y"),
                font_size=font_size,
                color=_short_str(it.get("color", "#ffffff"), name=f"{name}.color", limit=MAX_COLOR_CHARS),
            )
        )

    audio: list[AudioItem] = []
    for idx, it in enumerate(grouped.get("audio", [])):
        name = f"audio[{idx}]"
        start, end = _placed(it, name)
        gain = _f(it.get("gain", 1.0), name=f"{name}.gain")
        if not 0.0 <= gain <= MAX_AUDIO_GAIN:
            raise TimelineError(f"{name}.gain must be in [0, {MAX_AUDIO_GAIN}], got {gain}")
        src = it.get("src")
        path = resolve_audio_src(clip_folder, src)
        audio.append(AudioItem(start=start, end=end, src=str(src), path=str(path), gain=gain))

    if version == 2:
        duration = reel_end or 0.0
    else:
        # Default to the rightmost end across all items.
        duration = 0.0
        for item in (*video, *captions, *overlays):
            duration = max(duration, item.end)

    return RenderPlan(
        duration=duration,
        video=video,
        captions=captions,
        overlays=overlays,
        schema_version=version,
        segments=segments,
        audio=audio,
        caption_style=caption_style,
        mute_source=mute_source,
        source_path=source_path if version == 2 else None,
    )


def _segments(items: list[dict[str, Any]], source_range: tuple[float, float] | None) -> list[SourceSegment]:
    """v2 video items as source segments (module docstring)."""
    if not items:
        raise TimelineError("a v2 timeline needs at least one video segment")
    segments: list[SourceSegment] = []
    total = 0.0
    for idx, it in enumerate(items):
        name = f"video[{idx}]"
        src = it.get("src", "main")
        if src not in (None, "main"):
            raise TimelineError(f"{name}.src: a v2 segment reads the clip's source video; only 'main' is allowed")
        start, end = _times(it, name=name, allow_zero=False)
        if source_range is not None:
            lo, hi = source_range
            if start < lo - TIME_TOLERANCE or end > hi + TIME_TOLERANCE:
                raise TimelineError(f"{name}: [{start}, {end}] is outside the source [{lo:.3f}, {hi:.3f}]")
            start, end = max(start, lo), min(end, hi)
        if end - start < MIN_SEGMENT_SECONDS:
            raise TimelineError(f"{name}: shorter than {MIN_SEGMENT_SECONDS}s")
        if segments and start < segments[-1].end - TIME_TOLERANCE:
            raise TimelineError(
                f"{name}: segments must be in source order and must not overlap"
                f" (starts at {start}, previous ends at {segments[-1].end})"
            )
        if segments:
            start = max(start, segments[-1].end)
        total += end - start
        if total > MAX_OUTPUT_SECONDS:
            raise TimelineError(f"the edited reel is longer than {MAX_OUTPUT_SECONDS:.0f}s")
        segments.append(SourceSegment(start=start, end=end))
    return segments


def validate_timeline(
    timeline: dict[str, Any],
    *,
    source_range: tuple[float, float] | None = None,
    clip_folder: str | Path | None = None,
) -> int:
    """Check a timeline before it is stored; its schema version.

    Raises ``TimelineError`` (a clear message) on bad input. The same checks
    as ``build_render_plan``, bar one: a v1 timeline may hold zero-length
    items (the web editor's placeholder video item).
    """
    plan = _parse(
        timeline,
        "main",
        source_path=None,
        source_range=source_range,
        clip_folder=clip_folder,
        lenient_v1=True,
    )
    return plan.schema_version


def build_render_plan(
    timeline: dict[str, Any],
    base_clip_path: str,
    *,
    bus: "AsyncEventBus | None" = None,
    job_id: str | None = None,
    source_path: str | None = None,
    source_range: tuple[float, float] | None = None,
    clip_folder: str | Path | None = None,
) -> RenderPlan:
    """Validate and shape a timeline JSON into a RenderPlan.

    ``base_clip_path`` is the reel a v1 ``src: main`` reads. For v2,
    ``source_path`` is the source video the segments read, ``source_range``
    the source window they must lie in (``None``: unchecked), and
    ``clip_folder`` the folder voice-over ``src`` names resolve in.

    When ``bus`` + ``job_id`` are provided, a ``TIMELINE_EDITED`` event
    is scheduled on the successful build (mirrors the UI editor's
    save → timeline-shaped lifecycle).
    """
    plan = _parse(
        timeline,
        base_clip_path,
        source_path=source_path,
        source_range=source_range,
        clip_folder=clip_folder,
        lenient_v1=False,
    )
    emit_from_sync(
        bus,
        job_id,
        EventType.TIMELINE_EDITED,
        {
            "duration": plan.duration,
            "video_items": len(plan.video),
            "caption_items": len(plan.captions),
            "overlay_items": len(plan.overlays),
        },
    )
    return plan
