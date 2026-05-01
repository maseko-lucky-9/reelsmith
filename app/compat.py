"""Compatibility shims for third-party libraries.

Import this module before any MoviePy import to paper over API breakages
that haven't been fixed upstream yet.

  - PIL.Image.ANTIALIAS was removed in Pillow 10.0.0 (use LANCZOS instead).
    MoviePy 1.0.3 still references it, so we restore the alias at import time.
"""
from __future__ import annotations

from PIL import Image

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.LANCZOS  # type: ignore[attr-defined]
