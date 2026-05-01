import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.settings import settings

import app.logging_config  # noqa: F401

log = logging.getLogger(__name__)


def _load_font(font_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = settings.font_path
    if font_path and Path(font_path).is_file():
        try:
            return ImageFont.truetype(font_path, font_size)
        except OSError as e:
            log.warning("Failed to load font %s: %s", font_path, e)
    log.warning("Falling back to PIL default font; %s not found", font_path)
    return ImageFont.load_default()


def create_subtitle_image(text: str, videosize: tuple[int, int], font_size: int = 50) -> np.ndarray:
    log.info("Create Subtitle Image (%dx%d, font_size=%d)", videosize[0], videosize[1], font_size)
    img = Image.new("RGBA", videosize, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_size)

    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width = right - left
    text_height = bottom - top
    text_position = ((videosize[0] - text_width) / 2, videosize[1] - text_height - 10)

    bg_height = text_height + 10
    bg_top = videosize[1] - bg_height
    draw.rectangle([(0, bg_top), (videosize[0], videosize[1])], fill=(0, 0, 0, 255))
    draw.text(text_position, text, font=font, fill=(255, 255, 255, 255))

    return np.array(img)


def render_to_path(text: str, videosize: tuple[int, int], path: str, font_size: int = 50) -> str:
    log.debug("Rendering subtitle image  text=%r  size=%s  path=%s", text[:40], videosize, path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    arr = create_subtitle_image(text, videosize, font_size=font_size)
    Image.fromarray(arr, mode="RGBA").save(path)
    return path
