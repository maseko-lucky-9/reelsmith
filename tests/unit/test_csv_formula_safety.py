"""CSV formula injection (spec 014 security review), and where it must not apply.

Titles and summaries now come from a vision model and from transcripts, so a
clip can be titled ``=HYPERLINK(...)``. The bulk-export zip's manifest is
opened in spreadsheets: its cells are neutralised. The job's
``manifest.csv`` is machine read (the Social Publish workflow posts its
``description`` as the caption; ``scripts/map_inbox_to_db.py`` re-imports
it): it stays raw, so "@NASA just launched" is posted as written.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.routers.bulk_export import _build_manifest, csv_safe
from app.services.manifest_service import write_manifest
from scripts import map_inbox_to_db as mapper

HOSTILE = ['=HYPERLINK("http://x","y")', "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"]
CAPTIONS = ("@NASA just launched", "-40 degrees", "+3 tips")


@pytest.mark.parametrize("value", HOSTILE)
def test_formula_like_text_is_quoted(value):
    assert csv_safe(value) == "'" + value


@pytest.mark.parametrize("value", ["A red car", "", "1+1", 3, -2.5, None])
def test_other_values_are_unchanged(value):
    assert csv_safe(value) == value


def test_bulk_manifest_neutralises_title_and_summary():
    clip = SimpleNamespace(
        id="c1", title="+HYPERLINK(1)", summary="-2+3", start=0.0, end=4.0, output_path="/x.mp4",
        thumbnail_path=None, virality_score=50, hashtags=["#a"], broll_assets=None,
    )  # fmt: skip
    rows = list(csv.reader(io.StringIO(_build_manifest([clip]).decode("utf-8"))))
    assert rows[1][1:3] == ["'+HYPERLINK(1)", "'-2+3"]
    assert rows[1][3] == "0.0"


@pytest.mark.parametrize("text", CAPTIONS)
def test_bulk_manifest_quotes_captions_a_spreadsheet_would_run(text):
    clip = SimpleNamespace(
        id="c1", title=text, summary=text, start=0.0, end=4.0, output_path="/x.mp4",
        thumbnail_path=None, virality_score=50, hashtags=[], broll_assets=None,
    )  # fmt: skip
    rows = list(csv.reader(io.StringIO(_build_manifest([clip]).decode("utf-8"))))
    assert rows[1][1:3] == ["'" + text, "'" + text]


@pytest.mark.parametrize("text", CAPTIONS)
def test_the_job_manifest_keeps_captions_verbatim_through_the_inbox_reader(tmp_path: Path, text):
    """Review MEDIUM (round 2): a leading ' would be posted as part of the caption."""
    clip_file = tmp_path / "00_Clip.mp4"
    clip_file.write_bytes(b"\x00" * 64)
    clip = {"title": text, "summary": text, "start": 0, "end": 4, "hashtags": [], "export_path": str(clip_file)}
    write_manifest([clip], str(tmp_path))

    with open(tmp_path / "manifest.csv", newline="", encoding="utf-8") as f:
        (row,) = list(csv.DictReader(f))
    assert (row["title"], row["description"]) == (text, text)
    job = mapper._read_job(tmp_path)
    assert job is not None
    (read,) = job.clips
    assert (read.title, read.summary) == (text, text)
