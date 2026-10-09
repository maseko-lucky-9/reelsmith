"""Golden pixels for ``subtitle_image_service.create_subtitle_image``.

"Captions must look identical" across the ffmpeg port (P1) and the
dependency bumps (P2): every caption PNG must be byte-for-byte the image
today's renderer produces. Arguments mirror the real call chain
``clip_service.add_captions_to_clip`` → ``create_subtitle_clip`` →
``create_subtitle_image(text, (w, int(w / (9/16))), highlight_word_index=i,
text_anchor_y=band_anchor_y)`` with the default ``font_size=96`` and the
bundled Anton font.

Hashes are keyed by ``PIL.features.check("raqm")``: raqm (complex layout)
and Pillow's basic layout place glyphs differently (3,903 px differ for the
1920-wide cases below), and
the CI image may lack fribidi, which disables raqm.
"""

from __future__ import annotations

import hashlib
import platform

import PIL
import pytest
from PIL import features

from app.services import subtitle_image_service
from app.settings import _REPO_ANTON

_TEXT = "the quick fox"

# name -> (videosize, highlight_word_index, text_anchor_y)
# Anchors come from clip_service.py:193-195 for a 16:9 source of the same width
# (pinned in test_caption_entries_characterization.test_legacy_canvas_size_and_band_anchor).
CASES: dict[str, tuple[tuple[int, int], int | None, int]] = {
    "1920x3413_highlight_1": ((1920, 3413), 1, 2829),
    "1920x3413_no_highlight": ((1920, 3413), None, 2829),
    "320x568_highlight_0": ((320, 568), 0, 486),
}

# sha256 of create_subtitle_image(...).tobytes(), keyed by
# (raqm available, platform.system(), platform.machine()). Blur float rounding
# and text layout can differ across OS/CPU, so a hash is only trusted on the
# platform it was seeded on; every other platform skips (and prints the hashes
# it computed so a CI run can seed its own key). The platform-independent
# check that captions are unchanged is the P1 PNG-equality test.
GOLDEN_SHA256: dict[tuple[bool, str, str], dict[str, str]] = {
    # Seeded on macOS arm64: Pillow 12.2.0, FreeType 2.14.3, raqm 0.10.3.
    (True, "Darwin", "arm64"): {
        "1920x3413_highlight_1": "6771e4926d579fdd76131029776717cec68ac9d7a0607a88e5ffa27c8df9ed71",
        "1920x3413_no_highlight": "e834a9efd84ae06b5a0dbe5f15583c5f2340475c67d28910eb9598d61b8ae23a",
        "320x568_highlight_0": "56ddf006f6b82935734d85b7eec4028a4f7a296a71cae2e9881ebe8de76b7840",
    },
    # Seeded from the GitHub Actions ubuntu-latest runner (CI run 37939433692):
    # Pillow 12.3.0 manylinux wheel, FreeType 2.14.3, raqm 0.10.5. Identical to
    # the Darwin arm64 hashes; kept as its own key so a divergence on either
    # platform fails only there.
    (True, "Linux", "x86_64"): {
        "1920x3413_highlight_1": "6771e4926d579fdd76131029776717cec68ac9d7a0607a88e5ffa27c8df9ed71",
        "1920x3413_no_highlight": "e834a9efd84ae06b5a0dbe5f15583c5f2340475c67d28910eb9598d61b8ae23a",
        "320x568_highlight_0": "56ddf006f6b82935734d85b7eec4028a4f7a296a71cae2e9881ebe8de76b7840",
    },
    # For reference, forcing the BASIC
    # layout on the seeding Mac (ImageFont.core.HAVE_RAQM = False) gave
    #   1920x3413_highlight_1  0e4324b8bfc63f824062b388db9bb39b9531bad2af27d46f3936b99f6f9c9c97
    #   1920x3413_no_highlight e421e593842878e3332ab7f67f07c2ee67fd86c83f2ac114a00f612ae1f6b263
    #   320x568_highlight_0    56ddf006f6b82935734d85b7eec4028a4f7a296a71cae2e9881ebe8de76b7840
    # (3,903 px differ from raqm at 1920 wide; none at 320).
}


def _environment() -> str:
    return (
        f"raqm={features.check('raqm')} Pillow={PIL.__version__} "
        f"freetype={features.version('freetype2')} raqm_version={features.version('raqm')}"
    )


@pytest.fixture(autouse=True)
def _bundled_anton(monkeypatch):
    assert _REPO_ANTON.is_file(), f"bundled font missing: {_REPO_ANTON}"
    monkeypatch.setattr(subtitle_image_service.settings, "font_path", str(_REPO_ANTON))


def _render_sha256(case: str) -> str:
    videosize, highlight, anchor = CASES[case]
    arr = subtitle_image_service.create_subtitle_image(
        _TEXT, videosize, highlight_word_index=highlight, text_anchor_y=anchor
    )
    assert arr.shape == (videosize[1], videosize[0], 4)
    return hashlib.sha256(arr.tobytes()).hexdigest()


@pytest.mark.parametrize("case", sorted(CASES))
def test_subtitle_image_matches_golden_hash(case: str):
    key = (features.check("raqm"), platform.system(), platform.machine())
    actual = _render_sha256(case)
    expected = GOLDEN_SHA256.get(key, {}).get(case)
    if expected is None:
        message = (
            f"No golden hash for case {case!r} under raqm={key}; computed {actual}. "
            f"Seed GOLDEN_SHA256[{key}][{case!r}] = {actual!r}  ({_environment()})"
        )
        if key not in GOLDEN_SHA256:
            pytest.skip(message)
        pytest.fail(message)
    assert actual == expected, (
        f"Caption pixels changed for {case!r}: {actual} != golden {expected} ({_environment()})"
    )


def test_highlight_changes_pixels():
    """Guards against the cases collapsing to the same image."""
    assert _render_sha256("1920x3413_highlight_1") != _render_sha256(
        "1920x3413_no_highlight"
    )
