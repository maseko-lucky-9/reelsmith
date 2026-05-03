import csv
import json
from pathlib import Path

import pytest

from app.services.manifest_service import write_manifest, COLUMNS


def _read_csv(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_creates_manifest_csv_in_export_dir(tmp_path):
    path = write_manifest([], str(tmp_path))
    assert Path(path).name == "manifest.csv"
    assert Path(path).parent == tmp_path


def test_returns_path_string(tmp_path):
    result = write_manifest([], str(tmp_path))
    assert isinstance(result, str)


def test_csv_has_correct_headers(tmp_path):
    write_manifest([], str(tmp_path))
    with open(tmp_path / "manifest.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == COLUMNS


def test_empty_clips_writes_header_only(tmp_path):
    write_manifest([], str(tmp_path))
    rows = _read_csv(str(tmp_path / "manifest.csv"))
    assert rows == []


def test_clip_title_and_description_written(tmp_path):
    clip = {"title": "My Clip", "summary": "Great video", "hashtags": ["#go"], "start": 0, "end": 10}
    write_manifest([clip], str(tmp_path))

    rows = _read_csv(str(tmp_path / "manifest.csv"))
    assert rows[0]["title"] == "My Clip"
    assert rows[0]["description"] == "Great video"


def test_hashtags_serialized_as_json(tmp_path):
    clip = {"hashtags": ["#python", "#code"], "start": 0, "end": 5}
    write_manifest([clip], str(tmp_path))

    rows = _read_csv(str(tmp_path / "manifest.csv"))
    assert json.loads(rows[0]["hashtags"]) == ["#python", "#code"]


def test_duration_computed_from_start_end(tmp_path):
    clip = {"start": 10, "end": 40}
    write_manifest([clip], str(tmp_path))

    rows = _read_csv(str(tmp_path / "manifest.csv"))
    assert rows[0]["duration_seconds"] == "30"


def test_file_size_populated_when_export_path_exists(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 1_048_576)
    clip = {"export_path": str(video), "start": 0, "end": 10}
    write_manifest([clip], str(tmp_path))

    rows = _read_csv(str(tmp_path / "manifest.csv"))
    assert float(rows[0]["file_size_mb"]) == pytest.approx(1.0, rel=0.01)


def test_file_size_empty_when_export_path_missing(tmp_path):
    clip = {"export_path": str(tmp_path / "ghost.mp4"), "start": 0, "end": 5}
    write_manifest([clip], str(tmp_path))

    rows = _read_csv(str(tmp_path / "manifest.csv"))
    assert rows[0]["file_size_mb"] == ""


def test_none_hashtags_written_as_empty_json_array(tmp_path):
    clip = {"hashtags": None, "start": 0, "end": 5}
    write_manifest([clip], str(tmp_path))

    rows = _read_csv(str(tmp_path / "manifest.csv"))
    assert json.loads(rows[0]["hashtags"]) == []
