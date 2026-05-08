import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.settings import settings

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)

_GREEN = (46, 204, 64, 255)  # ≈ #2ECC40, matches example screenshot
_WHITE = (255, 255, 255, 255)
_BLACK = (0, 0, 0, 255)
_STROKE_WIDTH = 4
_PILL_RADIUS = 24
_PILL_PAD_X = 18
_PILL_PAD_Y = 8


def _load_font(font_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = settings.font_path
    if font_path and Path(font_path).is_file():
        try:
            return ImageFont.truetype(font_path, font_size)
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
) -> np.ndarray:
    log.info(
        "Create Subtitle Image (%dx%d, font_size=%d, highlight=%s, anchor_y=%s)",
        videosize[0], videosize[1], font_size, highlight_word_index, text_anchor_y,
    )
    text = text.upper()

    img = Image.new("RGBA", videosize, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)
    is_freetype = isinstance(font, ImageFont.FreeTypeFont)
    stroke_width = _STROKE_WIDTH if is_freetype else 0
    stroke_fill = _BLACK if is_freetype else None

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
    x = (videosize[0] - total_w) / 2

    # Pass 1: paint pill behind the active word so text overlays it cleanly.
    if highlight_word_index is not None and 0 <= highlight_word_index < len(words):
        pill_x = x + sum(word_widths[:highlight_word_index]) + space_w * highlight_word_index
        pill_w = word_widths[highlight_word_index]
        draw.rounded_rectangle(
            [
                (pill_x - _PILL_PAD_X, text_y - _PILL_PAD_Y),
                (pill_x + pill_w + _PILL_PAD_X, text_y + text_height + _PILL_PAD_Y),
            ],
            radius=_PILL_RADIUS,
            fill=_GREEN,
        )

    # Pass 2: draw all words in white with black stroke (active word sits on the pill).
    for word, w in zip(words, word_widths):
        draw.text(
            (x, text_y),
            word,
            font=font,
            fill=_WHITE,
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
        text, videosize,
        font_size=font_size,
        highlight_word_index=highlight_word_index,
        text_anchor_y=text_anchor_y,
    )
    Image.fromarray(arr, mode="RGBA").save(path)
    return path
