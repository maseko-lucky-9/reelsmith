"""``manifest.csv`` ``duration_seconds`` of an edited reel (spec 016 Phase
4a): the length recorded at its render, not the clip's ``end - start``."""

from __future__ import annotations

import csv

from app.services import clip_words, manifest_service


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_an_edited_reel_reports_its_recorded_length(tmp_path):
    clips = tmp_path / "Talk-1234" / "clips"
    clips.mkdir(parents=True)
    edited, plain = clips / "00_A.mp4", clips / "01_B.mp4"
    edited.write_bytes(b"reel")
    plain.write_bytes(b"reel")
    clip_words.write_reel_duration(str(edited), 4.5)
    export = tmp_path / "export"
    export.mkdir()

    path = manifest_service.write_manifest(
        [
            {"start": 100.0, "end": 110.0, "output_path": str(edited)},
            {"start": 20.0, "end": 26.0, "output_path": str(plain)},
            {"start": 1.0, "end": 3.0},
        ],
        str(export),
    )

    assert [float(r["duration_seconds"]) for r in _rows(path)] == [4.5, 6.0, 2.0]
