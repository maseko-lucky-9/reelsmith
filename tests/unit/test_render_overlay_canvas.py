"""``write_overlay_images`` draws each text overlay within the reel's canvas
(spec 016 Phase 4a, security M1): the canvas size reaches the drawing, so a
huge overlay is wrapped and bounded, never a PNG larger than the reel."""

from __future__ import annotations

from PIL import Image

from app.services import clip_service
from app.services.render_service import write_overlay_images
from app.services.timeline_render_service import TextOverlayItem
from app.settings import _REPO_ANTON, settings


def test_a_huge_overlay_png_fits_the_canvas(tmp_path, monkeypatch):
    assert _REPO_ANTON.is_file()
    monkeypatch.setattr(settings, "font_path", str(_REPO_ANTON))
    geometry = clip_service.reel_geometry(640, 360)
    canvas_w, canvas_h = geometry.canvas_size
    item = TextOverlayItem(start=0.0, end=1.0, text="W" * 500, x=0.5, y=0.5, font_size=400)

    (placed,) = write_overlay_images([item], geometry, tmp_path)

    with Image.open(placed.path) as img:
        assert img.width <= canvas_w and img.height <= canvas_h
    assert 0 <= placed.x <= canvas_w and 0 <= placed.y <= canvas_h
