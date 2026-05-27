"""Extract a representative JPEG thumbnail from a rendered clip."""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_TARGET_W, _TARGET_H = 320, 569  # 9:16

_STROKE_WIDTH = 2
_TEXT_FILL = (255, 255, 255)     # white
_TEXT_STROKE = (0, 0, 0)         # black


class ThumbnailError(Exception):
    """Raised when thumbnail generation or composition fails."""


def generate_thumbnail(clip_path: str, output_path: str) -> str:
    """Extract a frame at the clip midpoint and save as JPEG.

    Tries opencv-python-headless first (fast), falls back to moviepy.
    Returns the output_path on success.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        return _via_cv2(clip_path, output_path)
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("cv2 thumbnail failed (%s), falling back to moviepy", e)

    return _via_moviepy(clip_path, output_path)


def _via_cv2(clip_path: str, output_path: str) -> str:
    import cv2  # type: ignore[import]

    cap = cv2.VideoCapture(clip_path)
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        mid = max(0, total // 2)
        cap.set(cv2.CAP_PROP_POS_FRAMES, mid)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"cv2 could not read frame {mid} from {clip_path}")
        h, w = frame.shape[:2]
        frame = _crop_to_ratio(frame, w, h)
        frame = cv2.resize(frame, (_TARGET_W, _TARGET_H))
        cv2.imwrite(output_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    finally:
        cap.release()
    return output_path


def _crop_to_ratio(frame, w: int, h: int):
    import cv2  # type: ignore[import]

    target_ratio = _TARGET_W / _TARGET_H
    current_ratio = w / h
    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        x0 = (w - new_w) // 2
        frame = frame[:, x0 : x0 + new_w]
    else:
        new_h = int(w / target_ratio)
        y0 = (h - new_h) // 2
        frame = frame[y0 : y0 + new_h, :]
    return frame


def _via_moviepy(clip_path: str, output_path: str) -> str:
    import app.compat  # noqa: F401

    from moviepy.editor import VideoFileClip  # type: ignore[import]
    from PIL import Image  # type: ignore[import]
    import numpy as np

    with VideoFileClip(clip_path) as clip:
        t = clip.duration / 2
        frame = clip.get_frame(t)  # numpy RGB array

    img = Image.fromarray(frame)
    w, h = img.size
    target_ratio = _TARGET_W / _TARGET_H
    current_ratio = w / h
    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        x0 = (w - new_w) // 2
        img = img.crop((x0, 0, x0 + new_w, h))
    else:
        new_h = int(w / target_ratio)
        y0 = (h - new_h) // 2
        img = img.crop((0, y0, w, y0 + new_h))
    img = img.resize((_TARGET_W, _TARGET_H), Image.LANCZOS)
    img.save(output_path, "JPEG", quality=85)
    return output_path


# ---------------------------------------------------------------------------
# Text-composite helper
# ---------------------------------------------------------------------------

def compose_thumbnail(
    clip_path: str,
    output_path: str,
    *,
    headline: str,
    font_path: str | None = None,
    position: str = "bottom",
) -> str:
    """Generate a thumbnail frame and optionally overlay a headline.

    Steps:
    1. Extract the midpoint frame via ``generate_thumbnail``.
    2. If *headline* is non-empty, composite the text using Pillow
       ``ImageDraw`` with a 2-px black stroke and white fill.

    Parameters
    ----------
    clip_path:
        Path to the source video clip.
    output_path:
        Destination JPEG path.
    headline:
        Text to render on the thumbnail.  Pass ``""`` for a plain frame
        (output is byte-identical to ``generate_thumbnail``).
    font_path:
        Optional path to a TrueType/OpenType font file.  Falls back to the
        Pillow built-in default font when ``None``.
    position:
        Vertical anchor – ``"bottom"`` (default) or ``"top"``.

    Returns
    -------
    str
        The resolved *output_path*.

    Raises
    ------
    ThumbnailError
        On any failure, including when Pillow is not importable.
    """
    # Step 1 — extract raw frame (re-uses existing cv2/moviepy fallback logic)
    try:
        generate_thumbnail(clip_path, output_path)
    except Exception as exc:
        raise ThumbnailError(f"Frame extraction failed: {exc}") from exc

    # Empty headline → return as-is (byte-identical to generate_thumbnail)
    if not headline:
        return output_path

    # Step 2 — overlay text with Pillow
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore[import]
    except ImportError as exc:
        raise ThumbnailError(
            "Pillow is required for text overlay but is not installed."
        ) from exc

    try:
        img = Image.open(output_path).convert("RGB")

        draw = ImageDraw.Draw(img)

        # Font selection
        if font_path:
            try:
                font = ImageFont.truetype(font_path, size=28)
            except Exception:
                font = ImageFont.load_default()
        else:
            font = ImageFont.load_default()

        # Measure text bounding box
        bbox = draw.textbbox((0, 0), headline, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        margin = 16
        x = (_TARGET_W - text_w) // 2

        if position == "top":
            y = margin
        else:  # "bottom" default
            y = _TARGET_H - text_h - margin

        # Draw stroke (black outline) by rendering text at offsets
        for dx in range(-_STROKE_WIDTH, _STROKE_WIDTH + 1):
            for dy in range(-_STROKE_WIDTH, _STROKE_WIDTH + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), headline, font=font, fill=_TEXT_STROKE)

        # Draw filled text on top
        draw.text((x, y), headline, font=font, fill=_TEXT_FILL)

        img.save(output_path, "JPEG", quality=85)
    except ThumbnailError:
        raise
    except Exception as exc:
        raise ThumbnailError(f"Text overlay failed: {exc}") from exc

    return output_path
