"""Saved sources must not live under the OS temp dir (T031).

macOS clears ``/tmp``, so with the old ``/tmp/yt`` default the retained source
videos vanished and re-render answered 409 "source video not retained".
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.domain.ids import new_job_id
from app.routers import uploads
from app.services.folder_service import create_video_subfolder
from app.services.platforms import upload as upload_adapter
from app.settings import Settings, settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default() -> Path:
    return Path(Settings.model_fields["default_download_path"].default)


def _is_under(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def test_default_download_path_is_not_under_the_temp_dir():
    default = _default()

    assert not str(default).startswith("/tmp")
    assert not _is_under(default, Path("/tmp"))
    assert not _is_under(default, Path(tempfile.gettempdir()))


def test_default_download_path_is_the_project_data_downloads_dir():
    assert _default() == PROJECT_ROOT / "data" / "downloads"


def test_data_downloads_is_gitignored():
    lines = (PROJECT_ROOT / ".gitignore").read_text().splitlines()

    assert "data/downloads/" in lines


def test_download_dir_is_created_on_demand(tmp_path):
    base = tmp_path / "data" / "downloads"

    _, clips = create_video_subfolder(
        str(base), "upload:///u/x.mp4", "upload", job_id=new_job_id()
    )

    assert base.is_dir()
    assert Path(clips).is_dir()


def test_uploads_are_stored_under_the_download_path():
    expected = Path(settings.default_download_path) / "uploads"

    assert uploads._UPLOAD_DIR == expected
    assert upload_adapter._UPLOAD_ROOT == expected.resolve()


def test_upload_adapter_reads_a_file_under_the_download_path(tmp_path):
    uploaded = Path(settings.default_download_path) / "uploads" / f"{new_job_id()}.mp4"
    uploaded.parent.mkdir(parents=True, exist_ok=True)
    uploaded.write_bytes(b"\x00")
    try:
        result = upload_adapter.UploadAdapter().download(
            f"upload://{uploaded}", str(tmp_path)
        )
    finally:
        uploaded.unlink()

    assert result.video_path == str(uploaded.resolve())


def test_upload_adapter_still_reads_the_legacy_tmp_root(tmp_path, monkeypatch):
    """Jobs uploaded before this change point at /tmp/yt/uploads."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    old = legacy / "old.mp4"
    old.write_bytes(b"\x00")
    monkeypatch.setattr(upload_adapter, "_LEGACY_UPLOAD_ROOT", legacy.resolve())

    result = upload_adapter.UploadAdapter().download(f"upload://{old}", str(tmp_path))

    assert result.video_path == str(old.resolve())


def test_upload_adapter_rejects_a_path_outside_both_roots(tmp_path):
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"\x00")

    with pytest.raises(PermissionError):
        upload_adapter.UploadAdapter().download(f"upload://{outside}", str(tmp_path))
