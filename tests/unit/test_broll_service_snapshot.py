"""Snapshot test for ``broll_service.LocalBRoll.find_broll``.

Locks the current matcher behaviour before Wave 1 reorders the
``broll_apply`` pipeline stage and adds a Pexels provider. Failures
here indicate behavioural drift in:

* noun-phrase extraction order (spaCy vs proper-noun fallback)
* keyword splitting / case-folding
* match precedence when multiple clips qualify
* glob ordering / case sensitivity (``*.mp4`` vs ``*.MP4``)

If a Wave 1 PR changes one of these on purpose, update this file
and call out the diff in the PR description.

T012 (B-roll wiring) changed three on purpose, and every case below still
holds: keywords are all lowercase content tokens of the text in spoken
order (no spaCy; the old fallback kept only Capitalised words, so a
lowercase query never matched), a keyword matches a whole word of the file
name (plural ``s`` folded) instead of any substring, and the library
(``settings.broll_library_dir``) is scanned in name order.

PR-0b · ADR-003 · `tasks/todo.md`
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.broll_service import LocalBRoll
from app.settings import settings


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def broll_library(tmp_path: Path, monkeypatch) -> Path:
    """A frozen B-Roll library with deterministic filenames."""
    clip_names = [
        "sunset_timelapse.mp4",
        "mountain_drone.mp4",
        "city_traffic.mp4",
        "ocean_waves.MP4",
        "dog_running.mp4",
        "machine_learning.mp4",
    ]
    for name in clip_names:
        (tmp_path / name).write_bytes(b"\x00")
    monkeypatch.setattr(settings, "broll_library_dir", str(tmp_path))
    return tmp_path


# ── Snapshot cases ───────────────────────────────────────────────────────────
#
# Each case = (label, transcript, forced_phrases, expected_match_substring_or_None)
#
# ``forced_phrases`` short-circuits ``_extract_noun_phrases``. When ``None``,
# we exercise the real ``_extract_noun_phrases`` (lowercase content tokens).

_SNAPSHOT_CASES: list[tuple[str, str, list[str] | None, str | None]] = [
    (
        "single_keyword_match",
        "A beautiful sunset over the mountains",
        ["sunset"],
        "sunset_timelapse",
    ),
    (
        "multiword_phrase_first_token_wins",
        "The wild dog running through the field",
        ["dog running"],
        "dog_running",
    ),
    (
        "case_folded_uppercase_extension_match",
        "Watch the ocean breathe",
        ["ocean"],
        "ocean_waves",
    ),
    (
        "no_phrases_returns_none",
        "anything",
        [],
        None,
    ),
    (
        "no_match_returns_none",
        "Talking about quantum chromodynamics",
        ["quantum chromodynamics"],
        None,
    ),
    (
        "first_phrase_with_match_wins_over_later",
        "Sunset over the mountain — drone footage of a dog",
        ["sunset", "mountain", "dog"],
        "sunset_timelapse",
    ),
    (
        "underscore_split_in_phrase",
        "machine_learning revolution",
        ["machine_learning"],
        "machine_learning",
    ),
    (
        "fallback_proper_noun_extraction",
        "John Smith talking about Machine Learning today",
        None,  # exercise real fallback path
        "machine_learning",
    ),
    (
        "lowercase_query_matches",
        "sunset",
        None,  # the planner's queries are lowercase single words
        "sunset_timelapse",
    ),
]


@pytest.mark.parametrize(
    "label,transcript,forced_phrases,expected",
    _SNAPSHOT_CASES,
    ids=[c[0] for c in _SNAPSHOT_CASES],
)
def test_find_broll_snapshot(
    broll_library: Path,
    monkeypatch,
    label: str,
    transcript: str,
    forced_phrases: list[str] | None,
    expected: str | None,
) -> None:
    svc = LocalBRoll()
    if forced_phrases is not None:
        monkeypatch.setattr(svc, "_extract_noun_phrases", lambda _t: forced_phrases)

    result = svc.find_broll(transcript)

    if expected is None:
        assert result is None, f"[{label}] expected None, got {result!r}"
    else:
        assert result is not None, f"[{label}] expected match containing {expected!r}, got None"
        assert expected in result, f"[{label}] expected substring {expected!r} in {result!r}"


def test_find_broll_returns_absolute_path(broll_library: Path) -> None:
    """Locked: ``find_broll`` returns a stringified path that resolves under the library."""
    svc = LocalBRoll()
    result = svc.find_broll("sunset")

    assert result is not None
    assert Path(result).parent == broll_library
    assert Path(result).suffix.lower() == ".mp4"


def test_find_broll_empty_text_returns_none(broll_library: Path, monkeypatch) -> None:
    svc = LocalBRoll()
    monkeypatch.setattr(svc, "_extract_noun_phrases", lambda _t: [])
    assert svc.find_broll("") is None
