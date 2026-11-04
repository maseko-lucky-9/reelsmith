import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

import app.logging_config  # noqa: F401
from app.settings import settings

log = logging.getLogger(__name__)

_GREEN = (46, 204, 64, 255)  # ≈ #2ECC40
_WHITE = (255, 255, 255, 255)
_BLACK = (0, 0, 0, 255)
_SHADOW_FILL = (0, 0, 0, 200)
_SHADOW_OFFSET = (4, 6)
_SHADOW_BLUR = 4
_STROKE_WIDTH = 4
DEFAULT_FONT_SIZE = 96

RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class CaptionStyle:
    """Look of a caption PNG. The defaults are the pipeline's own captions
    (white words, the active word green, black stroke, 96 px)."""

    font_size: int = DEFAULT_FONT_SIZE
    fill: RGBA = _WHITE
    highlight_fill: RGBA = _GREEN
    stroke_fill: RGBA = _BLACK
    highlights: bool = True
    """False: the caption is drawn whole, no word is ever highlighted."""


# The presets' font sizes are ASS sizes on a 1080x1920 script; the PNG
# captions draw "hormozi" (56) at the pipeline's own 96 px and scale the
# others by the same factor.
_PRESET_FONT_SCALE = DEFAULT_FONT_SIZE / 56


def _rgba(color: str | None, default: RGBA) -> RGBA:
    """``color`` (any PIL colour string, e.g. ``#ffcc00``) as opaque RGBA;
    ``default`` when it is missing or not a colour."""
    if not color:
        return default
    try:
        rgb = ImageColor.getrgb(color)
    except ValueError, TypeError:
        return default
    return (*rgb[:3], 255)


def caption_style_for_preset(name: str | None) -> CaptionStyle:
    """The ``animated_caption_service.PRESETS`` colour/size set ``name`` as a
    PNG caption style; ``None`` is the default look. Presets without a
    highlight colour (``static``, ``subtle``) never highlight a word."""
    if name is None:
        return CaptionStyle()
    from app.services.animated_caption_service import get_preset

    preset = get_preset(name)
    return CaptionStyle(
        font_size=max(1, round(preset.font_size * _PRESET_FONT_SCALE)),
        fill=_rgba(preset.primary_color, _WHITE),
        highlight_fill=_rgba(preset.highlight_color, _GREEN),
        stroke_fill=_rgba(preset.stroke_color, _BLACK),
        highlights=preset.highlight_color is not None,
    )


# Per-thread (path, size) -> FreeTypeFont cache. Fonts are not shared across
# threads: caption images are drawn from a thread pool and FreeType face
# objects are not safe for concurrent use.
_font_cache = threading.local()


def _load_font(font_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = settings.font_path
    if font_path and Path(font_path).is_file():
        cache = getattr(_font_cache, "fonts", None)
        if cache is None:
            cache = _font_cache.fonts = {}
        key = (font_path, font_size)
        font = cache.get(key)
        if font is not None:
            return font
        try:
            font = cache[key] = ImageFont.truetype(font_path, font_size)
            return font
        except OSError as e:
            log.warning("Failed to load font %s: %s", font_path, e)
    log.warning("Falling back to PIL default font; %s not found", font_path)
    return ImageFont.load_default()


def create_subtitle_image(
    text: str,
    videosize: tuple[int, int],
    font_size: int = 96,
    highlight_word_index: int | None = None,
    text_anchor_y: int | None = None,
    *,
    fill: RGBA = _WHITE,
    highlight_fill: RGBA = _GREEN,
    stroke_fill: RGBA = _BLACK,
) -> np.ndarray:
    log.info(
        "Create Subtitle Image (%dx%d, font_size=%d, highlight=%s, anchor_y=%s)",
        videosize[0],
        videosize[1],
        font_size,
        highlight_word_index,
        text_anchor_y,
    )
    text = text.upper()

    img = Image.new("RGBA", videosize, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)
    is_freetype = isinstance(font, ImageFont.FreeTypeFont)
    stroke_width = _STROKE_WIDTH if is_freetype else 0
    stroke_fill = stroke_fill if is_freetype else None

    # Measure full text for vertical positioning.
    _l, top, _r, bottom = draw.textbbox((0, 0), text, font=font)
    text_height = bottom - top
    if text_anchor_y is None:
        # Legacy fallback: bottom of canvas with margin.
        text_y = videosize[1] - text_height - 40
    else:
        text_y = int(text_anchor_y - text_height / 2)

    # Per-word geometry.
    words = text.split()
    space_bbox = draw.textbbox((0, 0), " ", font=font)
    space_w = space_bbox[2] - space_bbox[0]

    word_widths = []
    for word in words:
        wb = draw.textbbox((0, 0), word, font=font)
        word_widths.append(wb[2] - wb[0])

    total_w = sum(word_widths) + space_w * max(len(words) - 1, 0)
    x_start = (videosize[0] - total_w) / 2

    # Pass 1: blurred drop-shadow layer behind every word.
    shadow_layer = Image.new("RGBA", videosize, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    sx = x_start + _SHADOW_OFFSET[0]
    sy = text_y + _SHADOW_OFFSET[1]
    for word, w in zip(words, word_widths):
        shadow_draw.text((sx, sy), word, font=font, fill=_SHADOW_FILL)
        sx += w + space_w
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(_SHADOW_BLUR))
    img.alpha_composite(shadow_layer)
    draw = ImageDraw.Draw(img)

    # Pass 2: foreground words; the active word in the highlight colour.
    x = x_start
    for i, (word, w) in enumerate(zip(words, word_widths)):
        word_fill = highlight_fill if i == highlight_word_index else fill
        draw.text(
            (x, text_y),
            word,
            font=font,
            fill=word_fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        x += w + space_w

    return np.array(img)


def render_to_path(
    text: str,
    videosize: tuple[int, int],
    path: str,
    font_size: int = 96,
    highlight_word_index: int | None = None,
    text_anchor_y: int | None = None,
) -> str:
    log.debug("Rendering subtitle image  text=%r  size=%s  path=%s", text[:40], videosize, path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    arr = create_subtitle_image(
        text,
        videosize,
        font_size=font_size,
        highlight_word_index=highlight_word_index,
        text_anchor_y=text_anchor_y,
    )
    Image.fromarray(arr, mode="RGBA").save(path)
    return path


_OVERLAY_LINE_SPACING = 4
_MIN_OVERLAY_FONT_SIZE = 6
_OVERLAY_FIT_ATTEMPTS = 6
_OVERLAY_MAX_CANVASES = 4
"""An overlay block is never drawn larger than this many canvases a side."""


def _text_width(draw: ImageDraw.ImageDraw, text: str, font, stroke: int) -> int:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return right - left


def _wrap_overlay(text: str, draw: ImageDraw.ImageDraw, font, stroke: int, max_width: int) -> str:
    """``text`` with line breaks so no line is wider than ``max_width`` px:
    words move to the next line, a word wider than the line is cut."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if _text_width(draw, candidate, font, stroke) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            while len(word) > 1 and _text_width(draw, word, font, stroke) > max_width:
                cut = 1
                while cut < len(word) - 1 and _text_width(draw, word[: cut + 1], font, stroke) <= max_width:
                    cut += 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return "\n".join(lines)


def create_text_overlay_image(
    text: str,
    font_size: int,
    color: str | None,
    canvas_size: tuple[int, int] | None = None,
) -> Image.Image | None:
    """A tight RGBA image of ``text`` (a timeline text overlay): ``color``
    (white when missing or not a colour) with a black stroke. ``None`` when
    the text draws nothing. The text is drawn by PIL, never by ffmpeg, so it
    reaches no filtergraph.

    With ``canvas_size`` (the reel's ``(width, height)``) the text is wrapped
    to the canvas width; when the wrapped block is still taller than the
    canvas the font shrinks to fit, and an image still larger than the canvas
    is scaled down into it. So the image (and the memory it takes) is never
    larger than the canvas, whatever the text and font size.
    """
    if not text.strip():
        return None
    size = font_size
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for _attempt in range(_OVERLAY_FIT_ATTEMPTS):
        font = _load_font(size)
        stroke = max(1, size // 24) if isinstance(font, ImageFont.FreeTypeFont) else 0
        drawn = text
        if canvas_size is not None:
            drawn = _wrap_overlay(text, probe, font, stroke, max(1, canvas_size[0]))
        left, top, right, bottom = probe.multiline_textbbox(
            (0, 0), drawn, font=font, stroke_width=stroke, spacing=_OVERLAY_LINE_SPACING
        )
        if canvas_size is None or bottom - top <= canvas_size[1] or size <= _MIN_OVERLAY_FONT_SIZE:
            break
        shrink = canvas_size[1] / max(1, bottom - top)
        size = max(_MIN_OVERLAY_FONT_SIZE, min(size - 1, int(size * shrink**0.5)))
    if canvas_size is not None:
        # Memory guard: a block still far larger than the canvas (text no
        # timeline validation bounded) is cut before it is allocated.
        right = min(right, left + _OVERLAY_MAX_CANVASES * max(1, canvas_size[0]))
        bottom = min(bottom, top + _OVERLAY_MAX_CANVASES * max(1, canvas_size[1]))
    if right <= left or bottom <= top:
        return None
    img = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    ImageDraw.Draw(img).multiline_text(
        (-left, -top),
        drawn,
        font=font,
        fill=_rgba(color, _WHITE),
        stroke_width=stroke,
        stroke_fill=_BLACK if stroke else None,
        spacing=_OVERLAY_LINE_SPACING,
    )
    if img.getbbox() is None:
        return None
    if canvas_size is not None and (img.width > canvas_size[0] or img.height > canvas_size[1]):
        img.thumbnail((max(1, canvas_size[0]), max(1, canvas_size[1])))
    return img
