"""Drift guards for the editor preview's copies of render facts (spec 017 T151).

The clean-source preview (``web/src/editor/opencut/components/preview-panel.tsx``)
draws captions in the browser, so it carries copies of what the render uses:
the default caption font file, the caption looks of
``subtitle_image_service.caption_style_for_preset`` and a fixture of
``clip_service.reel_geometry`` cases that ``web/src/editor/preview.test.ts``
checks ``previewLayout.ts`` against. The render is the truth: each copy must
equal it, so a change on the Python side fails here until the web copy follows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from app import settings as settings_module
from app.services.animated_caption_service import PRESETS
from app.services.clip_service import reel_geometry
from app.services.subtitle_image_service import caption_style_for_preset

ROOT = Path(__file__).resolve().parents[2]
SERVER_FONTS = ROOT / "app" / "assets" / "fonts"
WEB_FONTS = ROOT / "web" / "public" / "fonts"
EDITOR = ROOT / "web" / "src" / "editor"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(value: dict) -> dict:
    """A dataclass dict as JSON reads it back (tuples become lists)."""
    return {k: list(v) if isinstance(v, tuple) else v for k, v in value.items()}


@pytest.mark.parametrize("name", ["Anton-Regular.ttf", "OFL.txt"])
def test_web_font_files_are_the_servers(name: str) -> None:
    assert _sha256(WEB_FONTS / name) == _sha256(SERVER_FONTS / name)


def test_the_copied_font_is_the_renders_default() -> None:
    # The preview font is "the font the server renders with by default" only
    # while the bundled Anton stays the first default candidate.
    assert settings_module._DEFAULT_FONT_CANDIDATES[0] == str(SERVER_FONTS / "Anton-Regular.ttf")


def test_editor_css_loads_the_copied_font() -> None:
    css = (EDITOR / "editor.css").read_text(encoding="utf-8")
    assert 'font-family: "Reelsmith Caption";' in css
    assert 'src: url("/fonts/Anton-Regular.ttf") format("truetype");' in css


def test_caption_looks_cover_the_default_and_every_preset() -> None:
    looks = json.loads((EDITOR / "caption-looks.json").read_text(encoding="utf-8"))
    assert set(looks) == {"default", *PRESETS}


@pytest.mark.parametrize("name", ["default", *PRESETS])
def test_caption_look_equals_caption_style_for_preset(name: str) -> None:
    looks = json.loads((EDITOR / "caption-looks.json").read_text(encoding="utf-8"))
    style = caption_style_for_preset(None if name == "default" else name)
    assert looks[name] == _plain(asdict(style))


def _geometry_cases() -> list[dict]:
    return json.loads((EDITOR / "reel-geometry-cases.json").read_text(encoding="utf-8"))


def test_geometry_cases_cover_landscape_and_portrait_sources() -> None:
    sizes = {tuple(case["source_size"]) for case in _geometry_cases()}
    # Landscape sources anchor in the blur band; already-9:16 and taller ones
    # have no band and use the 85% fallback (PR #96).
    assert {(1920, 1080), (576, 1024), (1080, 1920), (1080, 2340)} <= sizes


@pytest.mark.parametrize("case", _geometry_cases(), ids=lambda c: "x".join(map(str, c["source_size"])))
def test_geometry_case_equals_reel_geometry(case: dict) -> None:
    assert case == _plain(asdict(reel_geometry(*case["source_size"])))
