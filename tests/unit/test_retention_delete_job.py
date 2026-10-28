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
import unicodedata
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
    job = await _job(store, JOB, destination_folder=str(paths["folder"]), video_path=str(paths["source"]))
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


async def test_an_upload_that_failed_before_its_download_step_keeps_its_file(root):
    """``video_path`` is set by the upload adapter at the download step;
    before it only the client's ``upload://`` URL names a file, and a URL is
    never trusted for a delete (threat T1). The file stays, unclaimed."""
    store = InMemoryJobStore()
    source = _file(root / "uploads" / "9f8e.mp4")
    job = await _job(store, JOB, url=f"upload://{source}", status="failed")

    assert await delete_job_files(job, store) == []
    assert source.exists()


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


# ── Threat T1: a client-typed upload:// URL never deletes anything ────────────


def _case_insensitive(path: Path) -> bool:
    probe = path / "CaseProbe"
    probe.write_bytes(b"")
    try:
        return (path / "caseprobe").exists()
    finally:
        probe.unlink()


@pytest.mark.parametrize("target", ["unclaimed upload", "other job export", "other job sidecar", "other job source"])
async def test_an_upload_url_alone_never_deletes_another_jobs_file(root, target):
    """The auditor's PoC: a job whose ``upload://`` URL names any file below
    the download path fails (or is cancelled) and is deleted. The file it
    named must survive, whether or not another job's row names it."""
    store = InMemoryJobStore()
    other = _url_job_folder(root, OTHER)
    await _job(store, OTHER, destination_folder=str(other["folder"]), video_path=str(other["source"]))
    victim = {
        "unclaimed upload": _file(root / "uploads" / "someone.mp4"),
        "other job export": other["manifest"],
        "other job sidecar": other["sidecar"],
        "other job source": other["source"],
    }[target]
    job = await _job(store, JOB, url=f"upload://{victim}", status="failed")

    assert await delete_job_files(job, store) == []
    assert victim.exists()


async def test_an_upload_source_whose_url_names_another_file_is_kept(root):
    store = InMemoryJobStore()
    recorded = _file(root / "uploads" / "recorded.mp4")
    named = _file(root / "uploads" / "named.mp4")
    job = await _job(store, JOB, url=f"upload://{named}", video_path=str(recorded))

    assert await delete_job_files(job, store) == []
    assert recorded.exists() and named.exists()


async def test_a_named_file_outside_the_job_folder_is_kept(root):
    """A clip row naming another folder's file (unreferenced there, such as
    an export copy) does not reach outside the job's own folder."""
    store = InMemoryJobStore()
    mine = _url_job_folder(root)
    elsewhere = _file(root / "Other-abcdef12" / "exports" / "00_Intro.mp4")
    job = await _job(store, JOB, destination_folder=str(mine["folder"]), output_paths=[str(elsewhere)])
    await store.upsert_clip(JOB, "c1", _set(output_path=str(elsewhere)))

    await delete_job_files(job, store)

    assert elsewhere.exists()
    assert not mine["folder"].exists()


# ── S2: spellings (case, Unicode form) and file identity ──────────────────────


async def test_a_protected_folder_spelled_in_another_case_is_never_walked(root, monkeypatch):
    store = InMemoryJobStore()
    monkeypatch.setattr(retention.settings, "export_base_folder", str(root / "share"))
    folder = root / "SHARE" / f"job-{JOB[:8]}"
    copy = _file(folder / "exp.mp4")
    job = await _job(store, JOB, destination_folder=str(folder), output_paths=[str(copy)])

    assert await delete_job_files(job, store) == []
    assert copy.exists()


async def test_a_file_another_job_names_in_another_unicode_form_is_kept(root):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    nfc = unicodedata.normalize("NFC", "Café.mp4")
    clip = _file(paths["folder"] / "clips" / nfc)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))
    await _job(store, OTHER)
    nfd_spelling = str(paths["folder"] / "clips" / unicodedata.normalize("NFD", "Café.mp4"))
    await store.upsert_clip(OTHER, "theirs", _set(output_path=nfd_spelling))

    await delete_job_files(job, store)

    assert clip.exists()
    assert not paths["source"].exists()


async def test_another_jobs_upload_spelled_in_another_case_is_kept(root):
    """On a case-insensitive file system ``ABC123.mp4`` *is* another job's
    ``abc123.mp4``: compared by identity, not spelling."""
    if not _case_insensitive(root):
        pytest.skip("needs a case-insensitive file system (APFS default)")
    store = InMemoryJobStore()
    theirs = _file(root / "uploads" / "abc123.mp4")
    await _job(store, OTHER, url=f"upload://{theirs}", video_path=str(theirs))
    mine = root / "uploads" / "ABC123.mp4"
    job = await _job(store, JOB, url=f"upload://{mine}", video_path=str(mine))

    await delete_job_files(job, store)

    assert theirs.exists()


def test_a_protected_folder_reached_by_identity_is_refused(root):
    """The protected folder is compared by identity, not only by spelling:
    with no spelling to match, the folder's identity alone refuses it."""
    if not _case_insensitive(root):
        pytest.skip("needs a case-insensitive file system (APFS default)")
    library = root / "Library"
    clip = _file(library / "beach.mp4")
    keep = retention._Keep(set(), set(), (), {retention._file_id(library)}, [])

    folder = retention._job_folder(JOB, str(root / "library"), (root,), keep, retention._Ancestry())

    assert folder is None
    assert clip.exists()


# ── S3: another job's words sidecar ───────────────────────────────────────────


async def test_another_jobs_words_sidecar_is_kept(root):
    """Jobs from before per-job folders could keep their source in this job's
    folder: its ``.words.json`` sidecar is named by no row, yet theirs."""
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    theirs = paths["folder"] / "Their Talk.mp4"
    sidecar = _file(paths["folder"] / "Their Talk.words.json")
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))
    await _job(store, OTHER, video_path=str(theirs))

    await delete_job_files(job, store)

    assert sidecar.exists()
    assert not paths["reel"].exists()


# ── S4: paths from rows are logged escaped ────────────────────────────────────


async def test_paths_from_rows_are_logged_with_repr(root, caplog):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    forged = "/elsewhere/x.mp4\nERROR forged log line"
    job = await _job(store, JOB, destination_folder=str(paths["folder"]), output_paths=[forged])

    with caplog.at_level("DEBUG", logger=retention.log.name):
        await delete_job_files(job, store)

    assert caplog.records
    assert not any("\n" in r.getMessage() for r in caplog.records)


# ── S5: only the path columns of the other jobs are read ──────────────────────


class _NoFullScanStore(InMemoryJobStore):
    async def list_jobs(self, *args: Any, **kwargs: Any):
        raise AssertionError("a job delete must not load every job")

    async def list_clips(self, job_id: str | None = None, *args: Any, **kwargs: Any):
        if job_id is None:
            raise AssertionError("a job delete must not load every clip")
        return await super().list_clips(job_id, *args, **kwargs)


async def test_a_delete_reads_only_path_references_not_every_row(root):
    store = _NoFullScanStore()
    paths = _url_job_folder(root)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))
    await _job(store, OTHER, video_path=str(paths["source"]))

    await delete_job_files(job, store)

    assert paths["source"].exists()
    assert not paths["reel"].exists()


# ── Defence in depth ──────────────────────────────────────────────────────────


async def test_a_job_folder_with_a_symlink_anywhere_in_its_path_deletes_nothing(root):
    store = InMemoryJobStore()
    real = root / "real"
    precious = _file(real / f"Talk-{JOB[:8]}" / "video.mp4")
    (root / "alias").symlink_to(real, target_is_directory=True)
    job = await _job(store, JOB, destination_folder=str(root / "alias" / f"Talk-{JOB[:8]}"))

    assert await delete_job_files(job, store) == []
    assert precious.exists()


async def test_a_job_folder_that_holds_a_managed_root_deletes_nothing(root):
    store = InMemoryJobStore()
    folder = root / f"Talk-{JOB[:8]}"
    upload = _file(folder / "uploads" / "someone.mp4")
    own = _file(folder / "video.mp4")
    job = await _job(store, JOB, destination_folder=str(folder), video_path=str(own))

    assert await delete_job_files(job, store, roots=(root, folder / "uploads")) == []
    assert upload.exists() and own.exists()


async def test_one_file_that_cannot_be_deleted_does_not_stop_the_rest(root):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    locked_dir = paths["tmp"].parent
    locked_dir.chmod(0o500)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))
    try:
        deleted = await delete_job_files(job, store)
    finally:
        locked_dir.chmod(0o700)

    assert paths["tmp"].exists()
    assert str(paths["source"]) in deleted and not paths["export"].exists()


# ── Every protected folder, inside a job folder ───────────────────────────────


@pytest.mark.parametrize(
    "setting",
    [
        "reframe_model_dir",
        "broll_library_dir",
        "broll_cache_dir",
        "tiktok_cookies_dir",
        "generate_brief_dir",
        "data/brand_assets",
    ],
)
async def test_a_protected_folder_inside_a_job_folder_is_never_walked_or_deleted(root, monkeypatch, setting):
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    if setting == "data/brand_assets":
        # Relative, like the brand-template router's root: resolved from the
        # working directory.
        monkeypatch.chdir(paths["folder"])
        inside = paths["folder"] / "data" / "brand_assets"
    else:
        inside = paths["folder"] / setting
        monkeypatch.setattr(retention.settings, setting, str(inside))
    precious = _file(inside / "keep.bin")
    job = await _job(store, JOB, destination_folder=str(paths["folder"]), output_paths=[str(precious)])

    await delete_job_files(job, store)

    assert precious.exists()
    assert paths["export"].exists(), "the folder walk must be refused"


def test_the_bundled_assets_are_protected_and_never_a_job_folder():
    assets = (Path(retention.__file__).resolve().parent.parent / "assets").resolve()
    assert assets in retention.protected_roots()
    keep = retention._Keep(set(), set(), retention.protected_roots(), set(), [])

    assert retention._job_folder(JOB, str(assets), (assets.parent,), keep, retention._Ancestry()) is None


async def test_a_file_that_is_another_jobs_file_under_another_name_is_kept(root):
    """Compared by identity at the moment of deletion: a hard link in this
    job's folder to another job's clip is that clip's file."""
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    theirs = _file(root / "Other-abcdef12" / "clips" / "00.mp4")
    alias = paths["folder"] / "clips" / "linked.mp4"
    os.link(theirs, alias)
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))
    await _job(store, OTHER)
    await store.upsert_clip(OTHER, "theirs", _set(output_path=str(theirs)))

    await delete_job_files(job, store)

    assert alias.exists()
    assert not paths["reel"].exists()


def test_protected_folders_are_compared_case_and_unicode_folded():
    share = Path("/data/Share/Café")
    assert retention._is_protected(Path("/data/SHARE/café/x.mp4"), (share,))
    assert retention._is_protected(Path(unicodedata.normalize("NFD", "/data/share/café")), (share,))
    assert not retention._is_protected(Path("/data/Shared/x.mp4"), (share,))


def test_a_keep_set_matches_every_spelling_of_a_name():
    nfd = unicodedata.normalize("NFD", "/d/Clips/Café.mp4")
    keep = retention._Keep(retention._spellings(nfd), set(), (), set(), [])

    assert keep.names_file(unicodedata.normalize("NFC", "/d/clips/CAFÉ.mp4"))
    assert not keep.names_file("/d/clips/Cafe.mp4")


def test_a_folder_that_will_hold_a_protected_folder_is_not_walked(root):
    """Lexically, before anything exists on disk to compare by identity."""
    folder = root / f"Talk-{JOB[:8]}"
    folder.mkdir()
    keep = retention._Keep(set(), set(), (folder / "broll",), set(), [])

    assert retention._may_walk(JOB, folder, keep, retention._Ancestry()) is False


async def test_a_protected_folder_spelled_in_another_case_inside_the_job_folder_stops_the_walk(root, monkeypatch):
    """The setting names the folder in another case than the job folder:
    no spelling matches, the walk meets it by identity and stops."""
    if not _case_insensitive(root):
        pytest.skip("needs a case-insensitive file system (APFS default)")
    store = InMemoryJobStore()
    paths = _url_job_folder(root)
    library = paths["folder"] / "broll"
    precious = _file(library / "beach.mp4")
    monkeypatch.setattr(retention.settings, "broll_library_dir", str(root / paths["folder"].name.upper() / "broll"))
    job = await _job(store, JOB, destination_folder=str(paths["folder"]))

    await delete_job_files(job, store)

    assert precious.exists()
    assert paths["export"].exists(), "the walk must stop at the protected folder"
