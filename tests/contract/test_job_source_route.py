"""``GET /jobs/{job_id}/source`` (spec 017, T140, contracts/source-video.md):
the job's source video with range support, 404 (one wording, no path) for any
path failure, 409 while the job is being deleted, and the app-level auth."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.models import JobState
from app.main import create_app
from app.services import retention
from app.settings import settings
from app.workers import orchestrator as orch

JOB = "job-src-0001"
DATA = bytes(i % 251 for i in range(1000))
NOT_RETAINED = {"detail": "source video not retained"}


@pytest.fixture(autouse=True)
def _clean_deleting():
    orch._deleting.clear()
    yield
    orch._deleting.clear()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = (tmp_path / "downloads").resolve()
    (path / "uploads").mkdir(parents=True)
    monkeypatch.setattr(retention.settings, "default_download_path", str(path))
    monkeypatch.setattr(retention.settings, "export_base_folder", str(tmp_path / "share"))
    return path


@pytest.fixture
def source(root: Path) -> Path:
    folder = root / JOB
    folder.mkdir()
    path = folder / "source.mp4"
    path.write_bytes(DATA)
    return path


@pytest.fixture
def client(root: Path) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _seed(client: TestClient, video_path: str | None) -> None:
    state = JobState(job_id=JOB, url="https://example.com/v", download_path="/unused")
    state.video_path = video_path
    client.portal.call(client.app.state.job_store.create, state)


def test_whole_file_without_range(client, source):
    _seed(client, str(source))
    r = client.get(f"/jobs/{JOB}/source")
    assert r.status_code == 200
    assert r.content == DATA
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-length"] == "1000"
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"] == 'inline; filename="source.mp4"'


def test_head_range(client, source):
    _seed(client, str(source))
    r = client.get(f"/jobs/{JOB}/source", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.content == DATA[:100]
    assert r.headers["content-range"] == "bytes 0-99/1000"
    assert r.headers["content-length"] == "100"
    assert r.headers["content-disposition"].startswith("inline")


def test_open_ended_range_is_the_tail(client, source):
    _seed(client, str(source))
    r = client.get(f"/jobs/{JOB}/source", headers={"Range": "bytes=990-"})
    assert r.status_code == 206
    assert r.content == DATA[-10:]


def test_end_past_the_file_is_clamped(client, source):
    _seed(client, str(source))
    r = client.get(f"/jobs/{JOB}/source", headers={"Range": "bytes=900-99999"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 900-999/1000"


@pytest.mark.parametrize("header", ["bytes=" + "9" * 5000 + "-", "bytes=0-" + "9" * 5000])
def test_absurdly_long_range_numbers_get_the_whole_file_not_a_500(client, source, header):
    _seed(client, str(source))
    r = client.get(f"/jobs/{JOB}/source", headers={"Range": header})
    assert r.status_code == 200
    assert r.content == DATA


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=50-10"])
def test_unsatisfiable_range_is_416(client, source, header):
    _seed(client, str(source))
    r = client.get(f"/jobs/{JOB}/source", headers={"Range": header})
    assert r.status_code == 416
    assert r.headers["content-range"] == "bytes */1000"


def test_suffix_range_is_ignored(client, source):
    _seed(client, str(source))
    r = client.get(f"/jobs/{JOB}/source", headers={"Range": "bytes=-500"})
    assert r.status_code == 200
    assert r.content == DATA


def test_unknown_job_is_404(client, root):
    r = client.get("/jobs/nope/source")
    assert r.status_code == 404
    assert r.json() == {"detail": "job not found: nope"}


def test_no_video_path_is_404(client, root):
    _seed(client, None)
    r = client.get(f"/jobs/{JOB}/source")
    assert r.status_code == 404
    assert r.json() == NOT_RETAINED


def _assert_404_without_path(r, stored: str) -> None:
    assert r.status_code == 404
    assert r.json() == NOT_RETAINED
    assert stored.replace("\x00", "") not in r.text
    assert str(Path(stored).parent) not in r.text if stored else True


def test_missing_file_is_404(client, root):
    stored = str(root / JOB / "gone.mp4")
    _seed(client, stored)
    _assert_404_without_path(client.get(f"/jobs/{JOB}/source"), stored)


def test_a_directory_is_404(client, root):
    folder = root / JOB / "dir.mp4"
    folder.mkdir(parents=True)
    _seed(client, str(folder))
    _assert_404_without_path(client.get(f"/jobs/{JOB}/source"), str(folder))


def test_file_outside_the_download_folder_is_404(client, root, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(DATA)
    _seed(client, str(outside))
    _assert_404_without_path(client.get(f"/jobs/{JOB}/source"), str(outside))


def test_symlink_pointing_outside_is_404(client, root, tmp_path):
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(DATA)
    link = root / JOB / "link.mp4"
    link.parent.mkdir()
    os.symlink(outside, link)
    _seed(client, str(link))
    r = client.get(f"/jobs/{JOB}/source")
    _assert_404_without_path(r, str(link))
    assert "secret" not in r.text


@pytest.mark.parametrize(
    "stored",
    [
        "{root}/job/../../escape.mp4",
        "relative/source.mp4",
        "{root}/job/so\x00urce.mp4",
    ],
)
def test_unsafe_stored_paths_are_404(client, root, stored):
    stored = stored.format(root=root)
    _seed(client, stored)
    _assert_404_without_path(client.get(f"/jobs/{JOB}/source"), stored)


def test_job_being_deleted_is_409(client, source):
    _seed(client, str(source))
    assert orch.claim_delete(JOB)
    r = client.get(f"/jobs/{JOB}/source")
    assert r.status_code == 409
    assert r.json() == {"detail": "the job is being deleted"}


def test_api_prefix_answers_the_same(client, source):
    _seed(client, str(source))
    a = client.get(f"/jobs/{JOB}/source", headers={"Range": "bytes=0-9"})
    b = client.get(f"/api/jobs/{JOB}/source", headers={"Range": "bytes=0-9"})
    assert (b.status_code, b.content, b.headers["content-range"]) == (
        a.status_code,
        a.content,
        a.headers["content-range"],
    )


@pytest.mark.parametrize(
    ("name", "media_type"),
    [
        ("a.mp4", "video/mp4"),
        ("a.M4V", "video/mp4"),
        ("a.mov", "video/quicktime"),
        ("a.webm", "video/webm"),
        ("a.mkv", "video/x-matroska"),
        ("a.xyz", "application/octet-stream"),
    ],
)
def test_content_type_by_suffix(client, root, name, media_type):
    path = root / JOB / name
    path.parent.mkdir()
    path.write_bytes(DATA)
    _seed(client, str(path))
    assert client.get(f"/jobs/{JOB}/source").headers["content-type"] == media_type


def test_auth_required(root, source, monkeypatch):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", "test-key")
    with TestClient(create_app()) as client:
        _seed(client, str(source))
        url = f"/jobs/{JOB}/source"
        assert client.get(url).status_code == 401
        assert client.get(url, params={"token": "wrong"}).status_code == 401
        ok = client.get(url, params={"token": "test-key"}, headers={"Range": "bytes=0-9"})
        assert ok.status_code == 206
        bearer = client.get(url, headers={"Authorization": "Bearer test-key"})
        assert bearer.status_code == 200


def test_file_vanishing_after_the_checks_is_404_not_500(client, source, monkeypatch):
    """The file is deleted between the path checks and the open."""
    _seed(client, str(source))
    real_open = os.open

    def vanish(path, *a, **kw):
        if str(path) == str(source.resolve()):
            source.unlink()
        return real_open(path, *a, **kw)

    monkeypatch.setattr(os, "open", vanish)
    r = client.get(f"/jobs/{JOB}/source")
    assert r.status_code == 404
    assert r.json() == NOT_RETAINED


def test_non_latin1_source_name_is_200(client, root):
    folder = root / JOB
    folder.mkdir()
    path = folder / "旅｜日本：1.mp4"
    path.write_bytes(DATA)
    _seed(client, str(path))
    r = client.get(f"/jobs/{JOB}/source")
    assert r.status_code == 200
    assert r.content == DATA
    assert r.headers["content-disposition"].startswith("inline; filename=")
