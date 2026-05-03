from pathlib import Path

import pytest

from app.services.export_service import export_clips


def test_copies_clips_to_export_dir(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    export_dir = str(tmp_path / "exports")

    result = export_clips([str(src)], export_dir)

    assert len(result) == 1
    assert Path(result[0]).exists()
    assert Path(result[0]).name == "clip.mp4"


def test_creates_export_dir_if_missing(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    export_dir = str(tmp_path / "deep" / "exports")

    export_clips([str(src)], export_dir)

    assert Path(export_dir).is_dir()


def test_skips_none_paths(tmp_path):
    result = export_clips([None, None], str(tmp_path / "out"))
    assert result == []


def test_skips_none_within_mixed_list(tmp_path):
    src = tmp_path / "real.mp4"
    src.write_bytes(b"data")
    export_dir = str(tmp_path / "out")

    result = export_clips([None, str(src), None], export_dir)

    assert len(result) == 1
    assert Path(result[0]).name == "real.mp4"


def test_returns_destination_paths_not_source(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    export_dir = str(tmp_path / "exports")

    result = export_clips([str(src)], export_dir)

    assert result[0] != str(src)
    assert result[0].startswith(export_dir)


def test_empty_input_returns_empty_list(tmp_path):
    result = export_clips([], str(tmp_path / "out"))
    assert result == []
