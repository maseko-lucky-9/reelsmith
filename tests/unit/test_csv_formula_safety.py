"""CSV formula injection (spec 014 security review): manifest cells never start a formula.

Titles and summaries now come from a vision model and from transcripts, so a
clip can be titled ``=HYPERLINK(...)``; a spreadsheet opening the manifest must
see text.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers.bulk_export import _build_manifest
from app.services.manifest_service import csv_safe, write_manifest

HOSTILE = ['=HYPERLINK("http://x","y")', "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"]


@pytest.mark.parametrize("value", HOSTILE)
def test_formula_like_text_is_quoted(value):
    assert csv_safe(value) == "'" + value


@pytest.mark.parametrize("value", ["A red car", "", "1+1", 3, -2.5, None])
def test_other_values_are_unchanged(value):
    assert csv_safe(value) == value


def test_job_manifest_neutralises_title_and_summary(tmp_path: Path):
    clip = {"title": "=cmd|' /C calc'!A0", "summary": "@SUM(1)", "start": 0, "end": 4, "hashtags": []}
    write_manifest([clip], str(tmp_path))
    with open(tmp_path / "manifest.csv", newline="", encoding="utf-8") as f:
        (row,) = list(csv.DictReader(f))
    assert row["title"] == "'=cmd|' /C calc'!A0"
    assert row["description"] == "'@SUM(1)"
    assert row["duration_seconds"] == "4"


def test_bulk_manifest_neutralises_title_and_summary():
    clip = SimpleNamespace(
        id="c1", title="+HYPERLINK(1)", summary="-2+3", start=0.0, end=4.0, output_path="/x.mp4",
        thumbnail_path=None, virality_score=50, hashtags=["#a"], broll_assets=None,
    )  # fmt: skip
    rows = list(csv.reader(io.StringIO(_build_manifest([clip]).decode("utf-8"))))
    assert rows[1][1:3] == ["'+HYPERLINK(1)", "'-2+3"]
    assert rows[1][3] == "0.0"
