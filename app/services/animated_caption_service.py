"""Animated caption renderer (W2.1).

Five preset styles plus a `static` passthrough. Each style is described
by a dataclass with the parameters needed by the per-frame renderer:
animation kind, font, colours, stroke. The actual frame composition is
done by ``render_caption_frames`` which returns a list of (timestamp,
PIL.Image) pairs — fed into MoviePy as a transparent overlay clip in
production.

Tests assert on the descriptor + frame count + per-frame size, never
on pixel hashes.

T-06 (parity-wave) adds ``burn_animated_captions`` — an ffmpeg-driven
burn-in path that composites styled overlays onto an existing rendered
MP4 via Advanced SubStation Alpha (.ass) subtitles.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

log = logging.getLogger(__name__)

ANIMATION_KINDS = (
    "static",
    "hormozi",
    "mrbeast",
    "karaoke",
    "boldpop",
    "subtle",
)


@dataclass(frozen=True)
class CaptionStylePreset:
    name: str
    animation_kind: str
    font_size: int = 42
    primary_color: str = "#ffffff"
    highlight_color: str | None = None
    stroke_color: str | None = "#000000"


PRESETS: dict[str, CaptionStylePreset] = {
    "static": CaptionStylePreset(
        name="static", animation_kind="static",
        font_size=42, primary_color="#ffffff",
    ),
    "hormozi": CaptionStylePreset(
        name="hormozi", animation_kind="hormozi",
        font_size=56, primary_color="#ffffff",
        highlight_color="#fff200",
    ),
    "mrbeast": CaptionStylePreset(
        name="mrbeast", animation_kind="mrbeast",
        font_size=64, primary_color="#ffffff",
        highlight_color="#ff0066",
    ),
    "karaoke": CaptionStylePreset(
        name="karaoke", animation_kind="karaoke",
        font_size=48, primary_color="#ffffff",
        highlight_color="#22d3ee",
    ),
    "boldpop": CaptionStylePreset(
        name="boldpop", animation_kind="boldpop",
        font_size=60, primary_color="#ffffff",
        highlight_color="#7c3aed",
    ),
    "subtle": CaptionStylePreset(
        name="subtle", animation_kind="subtle",
        font_size=36, primary_color="#ffffff",
    ),
}


@dataclass(frozen=True)
class CaptionWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class CaptionFrame:
    timestamp: float
    text: str
    highlighted_word_index: int | None
    style: CaptionStylePreset


def get_preset(name: str) -> CaptionStylePreset:
    if name not in PRESETS:
        raise ValueError(
            f"unknown caption style: {name!r}; must be one of {sorted(PRESETS)}"
        )
    return PRESETS[name]


def plan_caption_frames(
    words: Iterable[CaptionWord],
    *,
    style: str = "hormozi",
    fps: int = 30,
) -> list[CaptionFrame]:
    """Deterministic 'render plan' for animated captions.

    Returns one frame per ``1/fps`` interval covering [first_word.start,
    last_word.end]. Each frame carries the caption text shown at that
    instant + the index of the currently-spoken word for highlight.
    """
    preset = get_preset(style)
    word_list = list(words)
    if not word_list:
        return []

    start = min(w.start for w in word_list)
    end = max(w.end for w in word_list)
    if end <= start:
        return []

    frame_dt = 1.0 / fps
    frames: list[CaptionFrame] = []
    t = start
    while t < end:
        # Show all words spoken up to (but not past) ``t + frame_dt``.
        visible: list[CaptionWord] = [w for w in word_list if w.start <= t]
        if not visible:
            t += frame_dt
            continue

        active_idx = None
        for i, w in enumerate(visible):
            if w.start <= t < w.end:
                active_idx = i
                break

        text = " ".join(w.text for w in visible[-7:])  # rolling window
        frames.append(
            CaptionFrame(
                timestamp=round(t, 4),
                text=text,
                highlighted_word_index=(
                    active_idx if active_idx is not None else None
                ),
                style=preset,
            )
        )
        t += frame_dt
    return frames


# ---------------------------------------------------------------------------
# T-06 — Styled burn-in via ffmpeg + libass (Advanced SubStation Alpha)
# ---------------------------------------------------------------------------


class AnimatedCaptionBurnError(RuntimeError):
    """Raised when the ffmpeg-driven caption burn-in step fails."""


@dataclass(frozen=True)
class _SrtCue:
    start: float
    end: float
    text: str


def _srt_time_to_seconds(stamp: str) -> float:
    """Parse a single SRT timestamp ``HH:MM:SS,mmm`` (or ``.mmm``) to seconds."""
    stamp = stamp.strip().replace(",", ".")
    hh, mm, ss = stamp.split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def _parse_srt(srt_path: str) -> list[_SrtCue]:
    """Parse a UTF-8 SRT file into a list of cues.

    Deliberately dependency-free (we don't reach for pysrt here) so the
    burn path can be exercised without extra imports; the SRT format is
    well-bounded and our cues come from our own caption_service.
    """
    raw = Path(srt_path).read_text(encoding="utf-8")
    cues: list[_SrtCue] = []
    # Normalise line endings then split on blank lines.
    blocks = [b for b in raw.replace("\r\n", "\n").strip().split("\n\n") if b.strip()]
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        # Find the timing line (first line containing "-->").
        timing_idx = next(
            (i for i, ln in enumerate(lines) if "-->" in ln), None
        )
        if timing_idx is None:
            continue
        timing = lines[timing_idx]
        try:
            start_s, end_s = [p.strip() for p in timing.split("-->")]
            start = _srt_time_to_seconds(start_s)
            end = _srt_time_to_seconds(end_s)
        except (ValueError, IndexError):
            continue
        text = " ".join(lines[timing_idx + 1 :]).strip()
        if end > start and text:
            cues.append(_SrtCue(start=start, end=end, text=text))
    return cues


def _seconds_to_ass_time(seconds: float) -> str:
    """Format seconds as ``H:MM:SS.cc`` (centiseconds), the .ass timing form."""
    seconds = max(0.0, seconds)
    hh = int(seconds // 3600)
    mm = int((seconds % 3600) // 60)
    ss = seconds - hh * 3600 - mm * 60
    # Two-digit centiseconds, matching libass' expected granularity.
    return f"{hh}:{mm:02d}:{ss:05.2f}"


def _ass_style_block(preset: CaptionStylePreset) -> str:
    """Render the [V4+ Styles] block from a CaptionStylePreset.

    ASS colours are ``&HAABBGGRR`` (little-endian BGR with alpha). We do
    a minimal conversion from ``#rrggbb`` here; full ASS styling is
    intentionally light — this is a stub-flavoured burn path.
    """
    def hex_to_ass(colour: str | None, fallback: str = "&H00FFFFFF") -> str:
        if not colour or not colour.startswith("#") or len(colour) != 7:
            return fallback
        r = colour[1:3]
        g = colour[3:5]
        b = colour[5:7]
        return f"&H00{b.upper()}{g.upper()}{r.upper()}"

    primary = hex_to_ass(preset.primary_color)
    secondary = hex_to_ass(preset.highlight_color, fallback=primary)
    outline = hex_to_ass(preset.stroke_color, fallback="&H00000000")
    return (
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{preset.font_size},{primary},{secondary},"
        f"{outline},&H64000000,-1,0,0,0,100,100,0,0,1,2,0,2,20,20,40,1\n"
    )


def _build_ass_document(
    cues: list[_SrtCue],
    *,
    style: str,
) -> tuple[str, list[str]]:
    """Build a full .ass document string + the list of Dialogue lines.

    For ``style="static"``: one Dialogue per cue (preserves the existing
    static behaviour — the same line shown for the full cue window).

    For all animated styles (e.g. ``hormozi``): we split each cue's text
    into words and STUB the word-level timing by distributing the words
    evenly across the cue's duration. One Dialogue line per WORD.
    """
    preset = get_preset(style)

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        f"{_ass_style_block(preset)}"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )

    dialogue_lines: list[str] = []

    if style == "static":
        # One Dialogue per cue — the existing static behaviour.
        for cue in cues:
            dialogue_lines.append(
                f"Dialogue: 0,{_seconds_to_ass_time(cue.start)},"
                f"{_seconds_to_ass_time(cue.end)},Default,,0,0,0,,"
                f"{cue.text}"
            )
    else:
        # STUB: distribute words evenly across the cue duration. Real
        # ASR-driven word timestamps are deferred to a future task; see
        # ``burn_animated_captions`` docstring.
        for cue in cues:
            words = [w for w in cue.text.split() if w]
            if not words:
                continue
            duration = cue.end - cue.start
            step = duration / len(words)
            for i, word in enumerate(words):
                w_start = cue.start + i * step
                w_end = cue.start + (i + 1) * step if i < len(words) - 1 else cue.end
                dialogue_lines.append(
                    f"Dialogue: 0,{_seconds_to_ass_time(w_start)},"
                    f"{_seconds_to_ass_time(w_end)},Default,,0,0,0,,"
                    f"{word}"
                )

    return header + "\n".join(dialogue_lines) + ("\n" if dialogue_lines else ""), dialogue_lines


def _invoke(argv: Sequence[str]) -> None:
    """Patchable subprocess hook (mirrors voiceover_service pattern)."""
    import subprocess

    log.info("animated-caption burn: %s", " ".join(argv))
    proc = subprocess.run(argv, capture_output=True)
    if proc.returncode != 0:
        raise AnimatedCaptionBurnError(
            f"ffmpeg failed (rc={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace')[-500:]}"
        )


def burn_animated_captions(
    clip_path: str,
    srt_path: str,
    output_path: str,
    style: str = "hormozi",
    *,
    invoker: Callable[[Sequence[str]], None] | None = None,
) -> str:
    """Burn styled captions onto ``clip_path`` and write to ``output_path``.

    STUB: real ASR-driven *word-level* timestamps are deferred. For
    animated styles (e.g. ``hormozi``) we synthesise per-word timing by
    splitting each SRT cue's text into words and distributing them
    evenly across the cue's duration — one .ass ``Dialogue:`` line per
    word. The real Opus-Clip experience uses 97%-accurate ASR
    word-timestamps; wiring that in is a future task. For
    ``style="static"`` we emit one Dialogue per cue (preserving the
    existing static caption behaviour).

    Args:
        clip_path: Source MP4 to overlay captions onto.
        srt_path: SRT file with cue-level timing + text.
        output_path: Destination MP4 path.
        style: Preset name from ``PRESETS`` (default ``hormozi``).
        invoker: Optional patchable ffmpeg subprocess hook used in tests.

    Returns:
        ``output_path`` on success.
    """
    # Validate preset early so callers get a clean error before ffmpeg.
    get_preset(style)

    cues = _parse_srt(srt_path)
    ass_text, _dialogues = _build_ass_document(cues, style=style)

    # Write the .ass file to a temp path; cleaned up on exit (success or
    # failure) so we don't leak intermediate subtitle artefacts.
    ass_fd, ass_path = tempfile.mkstemp(suffix=".ass", prefix="reelsmith-caption-")
    try:
        with open(ass_fd, "w", encoding="utf-8") as f:
            f.write(ass_text)

        argv = (
            "ffmpeg",
            "-y",
            "-i", clip_path,
            "-vf", f"ass={ass_path}",
            "-c:a", "copy",
            output_path,
        )
        run = invoker or _invoke
        run(argv)
        if not Path(output_path).is_file():
            raise AnimatedCaptionBurnError(
                f"ffmpeg: no output produced at {output_path}"
            )
        return output_path
    finally:
        try:
            Path(ass_path).unlink(missing_ok=True)
        except OSError:
            # Best-effort cleanup — never let temp-file unlink failures
            # mask the real ffmpeg outcome.
            log.warning("failed to remove temp .ass file: %s", ass_path)
