"""``retention.delete_job_files``: what ``DELETE /jobs/{id}`` removes from disk
and, above all, what it never touches (spec 013 FR-007..FR-010).

Deleted: the job's folder (source, sidecar, clips, ``_tmp``, ``exports/``)
and the files its rows name, an upload's source included. Kept: anything
outside the managed roots or in a protected folder (the export folder
first), an ``inbox://`` job's files, files another job names, anything
reached through a symlink, and the folder walk when the folder is shared,
unsafe or a symlink. Each test runs with the ``dir_fd`` walk and with the
re-resolve fallback of ``delete_below_root``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.bus.job_store import InMemoryJobStore
from app.domain.models import JobState
from app.services import retention
from app.services.retention import delete_job_files

JOB = "job-gone-0001"
OTHER = "job-kept-0002"


@pytest.fixture(params=["pinned", "by_path"], autouse=True)
def mode(request, monkeypatch) -> str:
    """Run every test with the dir_fd walk and with the re-resolve fallback."""
    monkeypatch.setattr(retention, "_PINNED", request.param == "pinned")
    return request.param


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The download path (a managed root, with ``uploads`` below it); the
    export folder is set, outside it, as in the owner's setup."""
    path = (tmp_path / "downloads").resolve()
    (path / "uploads").mkdir(parents=True)
    monkeypatch.setattr(retention.settings, "default_download_path", str(path))
    monkeypatch.setattr(retention.settings, "export_base_folder", str(tmp_path / "share"))
    return path


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    path = (tmp_path / "outside").resolve()
    path.mkdir()
    return path


def _file(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _set(**fields: Any):
    def _mutate(c: dict[str, Any]) -> None:
        c.update(fields)

    return _mutate


async def _job(
    store: InMemoryJobStore,
    job_id: str,
    *,
    url: str = "https://www.youtube.com/watch?v=x",
    status: str = "completed",
    **fields: Any,
) -> JobState:
    state = JobState(job_id=job_id, url=url, download_path="/unused", **fields)
    state.status = status  # type: ignore[assignment]
    return await store.create(state)


def _url_job_folder(root: Path, job_id: str = JOB) -> dict[str, Path]:
    """The layout a URL job leaves: ``<slug>-<job8>/`` with the source and its
    sidecar, ``clips/`` with a reel and thumbnail, a ``_tmp`` leftover and,
    without an export folder, ``exports/``."""
    folder = root / f"Some_Talk-{job_id[:8]}"
    return {
        "folder": folder,
        "source": _file(folder / "Some Talk.mp4"),
        "sidecar": _file(folder / "Some Talk.words.json"),
        "reel": _file(folder / "clips" / "00_Intro.mp4"),
        "thumb": _file(folder / "clips" / "00_Intro.jpg"),
        "tmp": _file(folder / "clips" / "_tmp" / job_id / "chapter_0.wav"),
        "export": _file(folder / "exports" / "00_Intro.mp4"),
        "manifest": _file(folder / "exports" / "manifest.json"),
    }


# ── What goes ─────────────────────────────────────────────────────────────────


async def test_a_url_job_loses_its_whole_folder_and_nothing_else(root):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    neighbour = _file(root / "Other-abcdef12" / "video.mp4")
    job = await _job(
        store,
        JOB,
        destination_folder=str(paths["folder"]),
        clips_folder=str(paths["folder"] / "clips"),
        video_path=str(paths["source"]),
        output_paths=[str(paths["reel"])],
    )
    await store.upsert_clip(JOB, "c1", _set(output_path=str(paths["reel"]), thumbnail_path=str(paths["thumb"])))

    deleted = await delete_job_files(job, store)

    assert not paths["folder"].exists()
    assert set(deleted) == {str(p) for k, p in paths.items() if k != "folder"}
    assert neighbour.exists()
    assert (root / "uploads").is_dir() and root.is_dir()


async def test_a_second_delete_finds_nothing_and_does_not_fail(root):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]), video_path=str(paths["source"]))

    await delete_job_files(job, store)

    assert await delete_job_files(job, store) == []


async def test_an_upload_source_goes_with_its_only_job(root):
    store = InMemoryJobStore()
    source = _file(root / "uploads" / "1b2c.mp4")
    folder = root / "uploads" / f"upload_video-{JOB[:8]}"
    reel = _file(folder / "clips" / "00_Full_Video.mp4")
    job = await _job(
        store,
        JOB,
        url=f"upload://{source}",
        destination_folder=str(folder),
        video_path=str(source),
    )
    await store.upsert_clip(JOB, "c1", _set(output_path=str(reel)))

    await delete_job_files(job, store)

    assert not source.exists()
    assert not folder.exists()
    assert (root / "uploads").is_dir()


# ── What is never touched ─────────────────────────────────────────────────────


async def test_export_folder_copies_are_never_deleted(root, tmp_path):
    store = InMemoryJobStore()
    share = tmp_path / "share" / JOB
    copy = _file(share / "00_Intro.mp4")
    manifest = _file(share / "manifest.json")
    paths = _url_job_folder(root)
    job = await _job(
        store,
        JOB,
        destination_folder=str(paths["folder"]),
        video_path=str(paths["source"]),
        output_paths=[str(copy)],
    )
    await store.upsert_clip(JOB, "c1", _set(output_path=str(copy)))

    await delete_job_files(job, store)

    assert copy.read_bytes() == b"x" and manifest.exists()
    assert not paths["folder"].exists()


async def test_an_export_folder_inside_the_download_path_is_still_never_touched(root, monkeypatch):
    store = InMemoryJobStore()
    share = root / "share"
    monkeypatch.setattr(retention.settings, "export_base_folder", str(share))
    copy = _file(share / JOB / "00_Intro.mp4")
    job = await _job(store, JOB, output_paths=[str(copy)])
    await store.upsert_clip(JOB, "c1", _set(output_path=str(copy)))

    assert await delete_job_files(job, store) == []
    assert copy.exists()


async def test_a_job_folder_that_holds_a_protected_folder_is_not_walked(root, monkeypatch):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    library = paths["folder"] / "broll"
    clip = _file(library / "beach.mp4")
    monkeypatch.setattr(retention.settings, "broll_library_dir", str(library))
    job = await _job(store, JOB, destination_folder=str(paths["folder"]), video_path=str(paths["source"]))

    await delete_job_files(job, store)

    assert clip.exists()
    # The job's own named file still goes; its unnamed files stay with the folder.
    assert not paths["source"].exists()
    assert paths["export"].exists()


async def test_an_inbox_job_keeps_every_file(root):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    job = await _job(
        store,
        JOB,
        url=f"inbox://{JOB}",
        destination_folder=str(paths["folder"]),
        video_path=str(paths["source"]),
        output_paths=[str(paths["reel"])],
    )
    await store.upsert_clip(JOB, "c1", _set(output_path=str(paths["reel"])))

    assert await delete_job_files(job, store) == []
    assert all(p.exists() for p in paths.values())


async def test_a_source_another_job_still_uses_is_kept(root):
    store = InMemoryJobStore()
    shared = _file(root / "uploads" / "shared.mp4")
    job = await _job(store, JOB, url=f"upload://{shared}", video_path=str(shared))
    await _job(store, OTHER, url=f"upload://{shared}", status="failed")

    await delete_job_files(job, store)

    assert shared.exists()


async def test_a_file_another_jobs_clip_names_is_kept_even_when_retired(root):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    job = await _job(store, JOB, video_path=str(paths["source"]))
    await store.upsert_clip(JOB, "mine", _set(output_path=str(paths["reel"])))
    await _job(store, OTHER)
    await store.upsert_clip(OTHER, "theirs", _set(output_path=str(paths["reel"]), thumbnail_path=str(paths["thumb"])))
    await store.retire_clips(OTHER, ["theirs"])

    await delete_job_files(job, store)

    assert paths["reel"].exists()
    assert not paths["source"].exists()


async def test_a_folder_shared_with_another_job_is_not_walked(root):
    """Jobs from before per-job folders (T030) could share ``<slug>/``: only
    the deleted job's own named files go, the other job's exports stay."""
    store = InMemoryJobStore()
    folder = root / "Same_Title"
    mine = _file(folder / "clips" / "00_Mine.mp4")
    theirs_export = _file(folder / "exports" / "00_Theirs.mp4")
    job = await _job(store, JOB, destination_folder=str(folder))
    await store.upsert_clip(JOB, "mine", _set(output_path=str(mine)))
    await _job(store, OTHER, destination_folder=str(folder))

    await delete_job_files(job, store)

    assert not mine.exists()
    assert theirs_export.exists()


async def test_symlinks_inside_the_job_folder_are_never_followed(root, outside):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    secret = _file(outside / "secret.txt")
    nested = _file(outside / "dir" / "precious.mp4")
    (paths["folder"] / "file-link.txt").symlink_to(secret)
    (paths["folder"] / "clips" / "dir-link").symlink_to(outside / "dir", target_is_directory=True)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))
    await store.upsert_clip(JOB, "c1", _set(output_path=str(paths["folder"] / "file-link.txt")))

    await delete_job_files(job, store)

    assert secret.read_bytes() == b"x"
    assert nested.read_bytes() == b"x"
    # The links themselves stay, so their folders stay; real files went.
    assert (paths["folder"] / "file-link.txt").is_symlink()
    assert not paths["reel"].exists() and not paths["source"].exists()


async def test_a_job_folder_that_is_a_symlink_out_of_the_root_deletes_nothing(root, outside):
    store = InMemoryJobStore()
    precious = _file(outside / "precious.mp4")
    link = root / f"Escape-{JOB[:8]}"
    link.symlink_to(outside, target_is_directory=True)
    job = await _job(store, JOB, destination_folder=str(link), video_path=str(link / "precious.mp4"))

    assert await delete_job_files(job, store) == []
    assert precious.exists() and link.is_symlink()


@pytest.mark.parametrize(
    "destination",
    [
        "{root}/../outside",
        "{outside}",
        "{root}/x/../../outside",
        "outside",
    ],
)
async def test_a_destination_folder_outside_the_managed_roots_deletes_nothing(root, outside, destination):
    store = InMemoryJobStore()
    precious = _file(outside / "precious.mp4")
    named = _file(outside / "named.mp4")
    raw = destination.format(root=root, outside=outside)
    job = await _job(
        store,
        JOB,
        destination_folder=raw,
        video_path=str(named),
        output_paths=[f"{root}/../outside/named.mp4"],
    )
    await store.upsert_clip(JOB, "c1", _set(output_path=f"{root}/../outside/precious.mp4"))

    assert await delete_job_files(job, store) == []
    assert precious.exists() and named.exists()


async def test_a_job_folder_that_is_a_managed_root_is_not_walked(root):
    store = InMemoryJobStore()
    other_source = _file(root / "Other-abcdef12" / "video.mp4")
    upload = _file(root / "uploads" / "someone-elses.mp4")
    job = await _job(store, JOB, destination_folder=str(root))

    assert await delete_job_files(job, store) == []
    assert other_source.exists() and upload.exists()


async def test_files_are_deleted_through_the_hardened_delete(root, monkeypatch):
    """Every removal goes through ``delete_below_root`` (re-validated at the
    moment of deletion), never a plain unlink or rmtree."""
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))
    calls: list[Path] = []
    real = retention.delete_below_root

    def spy(path: Path, roots: Any, **kw: Any) -> bool:
        calls.append(path)
        return real(path, roots, **kw)

    monkeypatch.setattr(retention, "delete_below_root", spy)

    await delete_job_files(job, store)

    assert set(calls) >= {paths["source"], paths["reel"], paths["folder"]}
    assert not paths["folder"].exists()


async def test_the_folder_walk_never_leaves_the_managed_roots(root, outside):
    """Even a job folder whose contents changed under it: an entry swapped
    for a symlink before the delete is refused, its target kept."""
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    target = _file(outside / "target.mp4")
    os.unlink(paths["reel"])
    paths["reel"].symlink_to(target)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]), output_paths=[str(paths["reel"])])

    await delete_job_files(job, store)

    assert target.read_bytes() == b"x"


async def test_a_symlink_to_a_file_elsewhere_in_the_root_keeps_its_target(root):
    """A link in the job folder (or a clip row naming a link) to a file of
    no other job, still inside the download path: the target is kept."""
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    target = _file(root / "unrelated" / "keep.mp4")
    named_target = _file(root / "unrelated" / "named.mp4")
    (paths["folder"] / "walk-link.mp4").symlink_to(target)
    (root / "named-link.mp4").symlink_to(named_target)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))
    await store.upsert_clip(JOB, "c1", _set(output_path=str(root / "named-link.mp4")))

    await delete_job_files(job, store)

    assert target.read_bytes() == b"x" and named_target.read_bytes() == b"x"
    assert not paths["source"].exists()


async def test_a_job_folder_that_is_a_symlink_inside_the_root_is_not_walked(root):
    store = InMemoryJobStore()
    real = root / "unrelated"
    keep = _file(real / "keep.mp4")
    link = root / f"Link-{JOB[:8]}"
    link.symlink_to(real, target_is_directory=True)
    job = await _job(store, JOB, destination_folder=str(link))

    assert await delete_job_files(job, store) == []
    assert keep.exists()


async def test_a_symlinked_folder_inside_the_job_folder_is_not_entered(root):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    keep = _file(root / "unrelated" / "keep.mp4")
    (paths["folder"] / "clips" / "dir-link").symlink_to(root / "unrelated", target_is_directory=True)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))

    await delete_job_files(job, store)

    assert keep.read_bytes() == b"x"
    assert not paths["reel"].exists()


async def test_a_dot_dot_folder_is_refused_even_when_it_lands_inside_the_root(root):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    job = await _job(store, JOB, destination_folder=f"{root}/elsewhere/../{paths['folder'].name}")

    assert await delete_job_files(job, store) == []
    assert all(p.exists() for p in paths.values())


async def test_an_upload_that_failed_before_its_download_step_still_loses_its_source(root):
    """``video_path`` is set at the download step; before it, only the
    ``upload://`` URL names the uploaded file."""
    store = InMemoryJobStore()
    source = _file(root / "uploads" / "9f8e.mp4")
    job = await _job(store, JOB, url=f"upload://{source}", status="failed")

    assert await delete_job_files(job, store) == [str(source)]
    assert not source.exists()


async def test_nothing_outside_the_managed_roots_is_even_attempted(root, outside, monkeypatch):
    """The first guard, not only ``delete_below_root``'s own re-check, keeps
    a path outside the roots away from the delete."""
    store = InMemoryJobStore()
    named = _file(outside / "named.mp4")
    job = await _job(store, JOB, video_path=str(named), destination_folder=str(outside))
    calls: list[Path] = []
    monkeypatch.setattr(retention, "delete_below_root", lambda path, roots, **kw: calls.append(path) or False)

    await delete_job_files(job, store)

    assert [p for p in calls if not p.is_relative_to(root)] == []
