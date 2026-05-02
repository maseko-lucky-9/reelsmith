import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.settings import settings

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)

_GREEN = (0, 255, 0, 255)
_WHITE = (255, 255, 255, 255)
_BLACK = (0, 0, 0, 255)
_STROKE_WIDTH = 2


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
    font_size: int = 50,
    highlight_word_index: int | None = None,
) -> np.ndarray:
    log.info(
        "Create Subtitle Image (%dx%d, font_size=%d, highlight=%s)",
        videosize[0], videosize[1], font_size, highlight_word_index,
    )
    text = text.upper()

    img = Image.new("RGBA", videosize, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)
    is_freetype = isinstance(font, ImageFont.FreeTypeFont)
    stroke_width = _STROKE_WIDTH if is_freetype else 0
    stroke_fill = _BLACK if is_freetype else None

    # Measure full text for background rectangle and vertical positioning.
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_height = bottom - top
    text_y = videosize[1] - text_height - 10

    bg_top = videosize[1] - text_height - 10
    draw.rectangle([(0, bg_top), (videosize[0], videosize[1])], fill=_BLACK)

    # Measure each word and space for per-word x-offsets.
    words = text.split()
    space_bbox = draw.textbbox((0, 0), " ", font=font)
    space_w = space_bbox[2] - space_bbox[0]

    word_widths = []
    for word in words:
        wb = draw.textbbox((0, 0), word, font=font)
        word_widths.append(wb[2] - wb[0])

    total_w = sum(word_widths) + space_w * max(len(words) - 1, 0)
    x = (videosize[0] - total_w) / 2

    for i, (word, w) in enumerate(zip(words, word_widths)):
        color = _GREEN if i == highlight_word_index else _WHITE
        draw.text(
            (x, text_y),
            word,
            font=font,
            fill=color,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        x += w + space_w

    return np.array(img)


def render_to_path(
    text: str,
    videosize: tuple[int, int],
    path: str,
    font_size: int = 50,
    highlight_word_index: int | None = None,
) -> str:
    log.debug("Rendering subtitle image  text=%r  size=%s  path=%s", text[:40], videosize, path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    arr = create_subtitle_image(text, videosize, font_size=font_size, highlight_word_index=highlight_word_index)
    Image.fromarray(arr, mode="RGBA").save(path)
    return path
