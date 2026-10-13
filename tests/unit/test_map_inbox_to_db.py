"""Mapping the export folder (the Syncthing inbox) into the database.

The inbox is ``<inbox>/<job_id>/<NN>_<title>.mp4`` plus ``manifest.csv``. A job
the database knows gets its dead clip paths re-pointed at the inbox copies; a
folder the database has never seen (a run on the old in-memory store, say) is
imported as a completed job. Nothing in the inbox is ever written: it is a
synced share, and an edit there would reach the user's other devices.
"""

from __future__ import annotations

import asyncio
import csv
import os
import unicodedata
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bus.job_store import SqlJobStore
from app.db.migrate import upgrade_to_head
from app.db.models import ClipRecord, JobRecord
from app.settings import settings
from scripts import map_inbox_to_db as mapper

HEADER = [
    "filename",
    "title",
    "duration_seconds",
    "file_size_mb",
    "description",
    "hashtags",
    "export_path",
    "thumbnail_path",
    "job_id",
    "broll_credits",
]
EXPORTED_AT = datetime(2026, 6, 2, 12, 30, tzinfo=UTC)


def _job_id() -> str:
    return uuid.uuid4().hex


def _make_job_folder(inbox: Path, job_id: str, rows: list[dict[str, Any]], *, with_files: bool = True) -> Path:
    """One export folder: an mp4 per row (unless told not to) and its manifest."""
    folder = inbox / job_id
    folder.mkdir(parents=True)
    with (folder / "manifest.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        for row in rows:
            full = {
                "filename": "",
                "title": "",
                "duration_seconds": "30.0",
                "file_size_mb": "1.0",
                "description": "",
                "hashtags": "[]",
                "export_path": "",
                "thumbnail_path": "",
                "job_id": job_id,
                "broll_credits": "",
            } | row
            writer.writerow(full)
            if with_files and full["filename"]:
                (folder / full["filename"]).write_bytes(b"mp4:" + full["filename"].encode())
    stamp = EXPORTED_AT.timestamp()
    os.utime(folder / "manifest.csv", (stamp, stamp))
    return folder


def _snapshot(root: Path) -> dict[str, tuple[int, float]]:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime) for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    path = tmp_path / "inbox"
    path.mkdir()
    (path / ".stfolder").mkdir()  # Syncthing's marker folder is not a job
    (path / "not-a-job-id").mkdir()
    return path


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[tuple[async_sessionmaker, Any]]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'map.db'}"
    await asyncio.to_thread(upgrade_to_head, url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def new_store() -> SqlJobStore:
        store = SqlJobStore()
        store._factory = factory
        return store

    yield factory, new_store
    await engine.dispose()


# -- reading the inbox --------------------------------------------------------


def test_read_inbox_parses_manifests_and_ignores_what_is_not_a_job(inbox):
    job = _job_id()
    _make_job_folder(
        inbox,
        job,
        [
            {
                "filename": "00_First Clip.mp4",
                "title": "First Clip",
                "duration_seconds": "65.0",
                "description": 'Has a comma, a "quote"\nand a newline.',
                "hashtags": '["#a", "b"]',
            },
            {"filename": "01_Second.mp4", "title": "Second", "duration_seconds": "n/a", "hashtags": "not json"},
        ],
    )

    (found,) = mapper.read_inbox(inbox)

    assert found.job_id == job
    assert found.exported_at == EXPORTED_AT
    first, second = found.clips
    assert (first.index, first.title, first.duration) == (0, "First Clip", 65.0)
    assert first.summary == 'Has a comma, a "quote"\nand a newline.'
    assert first.hashtags == ["#a", "b"]
    assert first.path == inbox / job / "00_First Clip.mp4"
    assert (second.duration, second.hashtags) == (None, [])


def test_read_inbox_skips_rows_without_a_file_and_says_why(inbox):
    job = _job_id()
    folder = _make_job_folder(
        inbox,
        job,
        [{"filename": "00_Here.mp4", "title": "Here"}, {"filename": "01_Gone.mp4", "title": "Gone"}, {"filename": ""}],
    )
    (folder / "01_Gone.mp4").unlink()

    (found,) = mapper.read_inbox(inbox)

    assert [c.filename for c in found.clips] == ["00_Here.mp4"]
    assert any("01_Gone.mp4" in reason for reason in found.skipped)
    assert any("no filename" in reason for reason in found.skipped)


def test_a_filename_that_is_not_a_plain_file_name_is_skipped(inbox):
    """The manifest comes from a synced share: a row must not reach outside its folder."""
    job = _job_id()
    folder = _make_job_folder(inbox, job, [{"filename": "00_Fine.mp4", "title": "Fine"}])
    (inbox / "secret.mp4").write_bytes(b"outside")
    with (folder / "manifest.csv").open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(["../secret.mp4", "Escapee", "1", "1", "", "[]", "", "", job, ""])

    (found,) = mapper.read_inbox(inbox)

    assert [c.filename for c in found.clips] == ["00_Fine.mp4"]
    assert any("not a plain file name" in reason for reason in found.skipped)


def test_a_folder_that_is_not_a_job_id_is_ignored_even_with_a_manifest(inbox):
    _make_job_folder(inbox, "old-export-by-hand", [{"filename": "00_A.mp4", "title": "A"}])
    job = _job_id()
    _make_job_folder(inbox, job, [{"filename": "00_B.mp4", "title": "B"}])

    assert [j.job_id for j in mapper.read_inbox(inbox)] == [job]


def test_duplicate_or_missing_number_prefixes_still_give_unique_chapter_indexes(inbox):
    """Chapter rows have no unique constraint, and upsert_chapter breaks on a duplicate."""
    _make_job_folder(
        inbox,
        _job_id(),
        [
            {"filename": "00_A.mp4", "title": "A"},
            {"filename": "00_B.mp4", "title": "B"},
            {"filename": "C.mp4", "title": "C"},
        ],
    )

    (found,) = mapper.read_inbox(inbox)

    assert sorted(c.index for c in found.clips) == [0, 1, 2]


# -- applying to the database ------------------------------------------------


async def test_a_dry_run_reports_and_writes_nothing(inbox, db):
    factory, _ = db
    _make_job_folder(inbox, _job_id(), [{"filename": "00_A.mp4", "title": "A"}])

    report = await mapper.map_inbox(factory, inbox, apply=False)

    assert report.imported_jobs == 1 and report.imported_clips == 1
    async with factory() as session:
        assert (await session.execute(select(JobRecord))).first() is None


async def test_a_folder_the_database_never_saw_becomes_a_completed_job(inbox, db):
    factory, new_store = db
    job = _job_id()
    _make_job_folder(
        inbox,
        job,
        [
            {
                "filename": "00_Alpha.mp4",
                "title": "Alpha",
                "duration_seconds": "40.5",
                "description": "About alpha",
                "hashtags": '["#alpha"]',
            },
            {"filename": "01_Beta.mp4", "title": "Beta", "duration_seconds": "20.0"},
        ],
    )

    report = await mapper.map_inbox(factory, inbox, apply=True)

    assert (report.imported_jobs, report.imported_clips) == (1, 2)
    state = await new_store().get(job)
    assert state.status == "completed"
    assert state.url == f"inbox://{job}"
    assert state.title == f"Inbox export {job[:8]}", "the job page shows the title, else 'Processing…'"
    assert (state.chapters_total, state.chapters_done) == (2, 2)
    assert state.clips_folder == str(inbox / job)
    assert {i: c.status for i, c in state.chapters.items()} == {0: "completed", 1: "completed"}
    assert state.chapters[0].output_path == str(inbox / job / "00_Alpha.mp4")
    clips = {c["title"]: c for c in await new_store().list_clips(job_id=job)}
    alpha = clips["Alpha"]
    assert (alpha["summary"], alpha["hashtags"], alpha["end"]) == ("About alpha", ["#alpha"], 40.5)
    assert alpha["output_path"] == str(inbox / job / "00_Alpha.mp4")
    assert alpha["retired"] is False and alpha["chapter_id"] is not None
    assert state.output_paths and sorted(state.output_paths) == sorted(c["output_path"] for c in clips.values())
    async with factory() as session:
        created = (await session.execute(select(JobRecord.created_at).where(JobRecord.id == job))).scalar_one()
    assert created.replace(tzinfo=UTC) == EXPORTED_AT, "the job is dated from its export, not from the import"


async def test_dead_clip_paths_of_a_known_job_are_pointed_at_the_inbox_copy(inbox, db, tmp_path):
    factory, new_store = db
    job = _job_id()
    _make_job_folder(
        inbox,
        job,
        [
            {"filename": "00_Dead.mp4", "title": "Dead"},
            {"filename": "01_Alive.mp4", "title": "Alive"},
            {"filename": "02_Retired.mp4", "title": "Retired"},
            {"filename": "", "title": "Enh"},
            {"filename": "04_Both.mp4", "title": "Both"},
            {"filename": "04_Both.enhanced.mp4", "title": "BothEnh"},
            {"filename": "05_Aud.mp4", "title": "Aud"},
        ],
    )
    (inbox / job / "03_Enh.mp4").write_bytes(b"exported before it was enhanced")
    alive_elsewhere = tmp_path / "01_Alive.mp4"
    alive_elsewhere.write_bytes(b"still here")
    async with factory() as session:
        session.add(JobRecord(id=job, youtube_url="https://yt.test/x", status="completed"))
        await session.flush()
        for name, path, retired in [
            ("Dead", "/tmp/yt/gone/00_Dead.mp4", False),
            ("Alive", str(alive_elsewhere), False),
            ("Retired", "/tmp/yt/gone/02_Retired.mp4", True),
            ("Enhanced", "/tmp/yt/gone/03_Enh.enhanced.mp4", False),
            ("ExactWins", "/tmp/yt/gone/04_Both.enhanced.mp4", False),
            ("EnhancedAudio", "/tmp/yt/gone/05_Aud.enhanced-audio.mp4", False),
            ("NoCopy", "/tmp/yt/gone/09_NoCopy.mp4", False),
        ]:
            session.add(ClipRecord(job_id=job, title=name, output_path=path, retired=retired))
        await session.commit()

    report = await mapper.map_inbox(factory, inbox, apply=True)

    assert (report.repointed, report.imported_jobs, report.retitled) == (5, 0, 0)
    assert (await new_store().get(job)).title is None, "a job that is not an inbox import keeps its (missing) title"
    async with factory() as session:
        rows = {c.title: c for c in (await session.execute(select(ClipRecord))).scalars()}
    assert rows["Dead"].output_path == str(inbox / job / "00_Dead.mp4")
    assert rows["Retired"].output_path == str(inbox / job / "02_Retired.mp4") and rows["Retired"].retired is True
    assert rows["Enhanced"].output_path == str(inbox / job / "03_Enh.mp4"), "only the pre-enhancement export was kept"
    assert rows["ExactWins"].output_path == str(inbox / job / "04_Both.enhanced.mp4"), (
        "an exact name beats the fallback"
    )
    assert rows["EnhancedAudio"].output_path == str(inbox / job / "05_Aud.mp4")
    assert rows["Alive"].output_path == str(alive_elsewhere), "a path that still resolves is never replaced"
    assert rows["NoCopy"].output_path == "/tmp/yt/gone/09_NoCopy.mp4", "no inbox copy: left as it was"
    assert (await new_store().get_clip(rows["Dead"].id))["output_path"] == str(inbox / job / "00_Dead.mp4")


async def test_a_folder_with_nothing_importable_is_reported_not_silently_dropped(inbox, db):
    factory, _ = db
    job = _job_id()
    _make_job_folder(inbox, job, [{"filename": "00_Gone.mp4", "title": "Gone"}], with_files=False)

    report = await mapper.map_inbox(factory, inbox, apply=True)

    assert report.imported_jobs == 0
    assert any(job[:8] in line and "nothing to import" in line for line in report.skipped)
    async with factory() as session:
        assert (await session.execute(select(JobRecord))).first() is None


async def test_an_imported_folder_reports_mp4_files_that_have_no_manifest_row(inbox, db):
    factory, _ = db
    job = _job_id()
    folder = _make_job_folder(inbox, job, [{"filename": "00_A.mp4", "title": "A"}])
    (folder / "01_Unlisted.mp4").write_bytes(b"x")

    report = await mapper.map_inbox(factory, inbox, apply=True)

    assert report.imported_clips == 1
    assert any("01_Unlisted.mp4" in line and "no manifest row" in line for line in report.skipped)


async def test_an_inbox_job_imported_without_a_title_gets_one_and_a_chosen_title_is_kept(inbox, db):
    factory, new_store = db
    untitled, titled = _job_id(), _job_id()
    for job in (untitled, titled):
        _make_job_folder(inbox, job, [{"filename": "00_A.mp4", "title": "A"}])
    async with factory() as session:
        session.add(JobRecord(id=untitled, youtube_url=f"inbox://{untitled}", status="completed", state={}))
        session.add(
            JobRecord(id=titled, youtube_url=f"inbox://{titled}", status="completed", state={"title": "My own"})
        )
        await session.commit()

    report = await mapper.map_inbox(factory, inbox, apply=True)

    assert report.retitled == 1
    assert (await new_store().get(untitled)).title == f"Inbox export {untitled[:8]}"
    assert (await new_store().get(titled)).title == "My own"


async def test_a_second_run_changes_nothing(inbox, db):
    factory, _ = db
    _make_job_folder(inbox, _job_id(), [{"filename": "00_A.mp4", "title": "A"}, {"filename": "01_B.mp4", "title": "B"}])
    await mapper.map_inbox(factory, inbox, apply=True)

    again = await mapper.map_inbox(factory, inbox, apply=True)

    assert (again.imported_jobs, again.imported_clips, again.repointed) == (0, 0, 0)
    async with factory() as session:
        assert len((await session.execute(select(ClipRecord))).scalars().all()) == 2


async def test_nothing_in_the_inbox_is_ever_modified(inbox, db):
    factory, _ = db
    _make_job_folder(inbox, _job_id(), [{"filename": "00_A.mp4", "title": "A"}])
    known = _job_id()
    _make_job_folder(inbox, known, [{"filename": "00_K.mp4", "title": "K"}])
    async with factory() as session:
        session.add(JobRecord(id=known, youtube_url="https://yt.test/k", status="completed"))
        await session.flush()
        session.add(ClipRecord(job_id=known, title="K", output_path="/tmp/yt/gone/00_K.mp4"))
        await session.commit()
    before = _snapshot(inbox)

    await mapper.map_inbox(factory, inbox, apply=True)

    assert _snapshot(inbox) == before


# -- review fixes: the inbox is a share the app must not damage or mis-read ----


def test_a_row_listed_twice_is_imported_once_and_reported(inbox):
    _make_job_folder(
        inbox, _job_id(), [{"filename": "00_A.mp4", "title": "A"}, {"filename": "00_A.mp4", "title": "A2"}]
    )

    (found,) = mapper.read_inbox(inbox)

    assert [c.title for c in found.clips] == ["A"]
    assert any("listed twice" in reason for reason in found.skipped)


async def test_a_dry_run_fails_where_apply_would_fail(inbox, db):
    """The dry run flushes, so a database constraint shows before --apply, not during it."""
    factory, _ = db
    job = _job_id()
    _make_job_folder(inbox, job, [{"filename": "00_A.mp4", "title": "A"}])
    async with factory() as session:  # another job already owns the id this clip would get
        session.add(JobRecord(id="other", youtube_url="https://yt.test/o"))
        await session.flush()
        session.add(ClipRecord(id=mapper._id(job, "00_A.mp4", "clip"), job_id="other"))
        await session.commit()

    with pytest.raises(IntegrityError):
        await mapper.map_inbox(factory, inbox, apply=False)


async def test_a_relative_inbox_is_stored_as_an_absolute_path(inbox, db, tmp_path, monkeypatch):
    factory, new_store = db
    job = _job_id()
    _make_job_folder(inbox, job, [{"filename": "00_A.mp4", "title": "A"}])
    monkeypatch.chdir(tmp_path)

    await mapper.map_inbox(factory, Path("inbox"), apply=True)

    (clip,) = await new_store().list_clips(job_id=job)
    assert Path(clip["output_path"]).is_absolute() and Path(clip["output_path"]).is_file()


async def test_an_inbox_below_a_managed_folder_is_refused(inbox, db, monkeypatch):
    """The janitor deletes below the download folder; mapped clips there would be its to delete."""
    factory, _ = db
    _make_job_folder(inbox, _job_id(), [{"filename": "00_A.mp4", "title": "A"}])
    monkeypatch.setattr(settings, "default_download_path", str(inbox.parent))

    with pytest.raises(mapper.InboxRefused, match="retention janitor"):
        await mapper.map_inbox(factory, inbox, apply=True)

    async with factory() as session:
        assert (await session.execute(select(JobRecord))).first() is None


def test_the_command_exits_2_for_an_inbox_below_a_managed_folder(inbox, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "default_download_path", str(inbox.parent))

    code = mapper.main(["--inbox", str(inbox), "--db-url", f"sqlite+aiosqlite:///{tmp_path / 'never-opened.db'}"])

    assert code == 2
    assert "retention janitor" in capsys.readouterr().err


async def test_a_file_name_matches_whichever_unicode_form_the_disk_manifest_or_database_uses(inbox, db):
    """macOS can keep 'é' as e + U+0301 on disk while the manifest or the database has it composed, or the reverse."""
    factory, new_store = db
    nfc = "13_Alizé Umbrella.mp4"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    disk_nfd, manifest_nfd, known = _job_id(), _job_id(), _job_id()
    for job, in_manifest, on_disk in [(disk_nfd, nfc, nfd), (manifest_nfd, nfd, nfc), (known, nfc, nfc)]:
        folder = _make_job_folder(inbox, job, [{"filename": in_manifest, "title": "Alizé"}], with_files=False)
        for stale in folder.glob("*.mp4"):
            stale.unlink()  # macOS treats both spellings as one file; leave only the one meant for the disk
        (folder / on_disk).write_bytes(b"mp4")
    async with factory() as session:
        session.add(JobRecord(id=known, youtube_url="https://yt.test/k", status="completed"))
        await session.flush()
        session.add(ClipRecord(job_id=known, title="Alizé", output_path=f"/tmp/yt/gone/{nfd}"))
        await session.commit()

    report = await mapper.map_inbox(factory, inbox, apply=True)

    assert (report.imported_clips, report.repointed) == (2, 1)
    assert not [r for r in report.skipped if "no manifest row" in r or "not in the folder" in r], report.skipped
    for job in (disk_nfd, manifest_nfd, known):
        (clip,) = await new_store().list_clips(job_id=job)
        assert Path(clip["output_path"]).is_file()
        assert unicodedata.normalize("NFC", Path(clip["output_path"]).name) == nfc


async def test_an_exact_decomposed_name_still_beats_the_pre_enhancement_fallback(inbox, db):
    factory, _ = db
    job = _job_id()
    enhanced = "04_Alizé.enhanced.mp4"
    folder = _make_job_folder(inbox, job, [{"filename": enhanced, "title": "E"}, {"filename": "04_Alizé.mp4"}])
    async with factory() as session:
        session.add(JobRecord(id=job, youtube_url="https://yt.test/x", status="completed"))
        await session.flush()
        session.add(ClipRecord(job_id=job, output_path="/tmp/yt/gone/" + unicodedata.normalize("NFD", enhanced)))
        await session.commit()

    await mapper.map_inbox(factory, inbox, apply=True)

    async with factory() as session:
        (path,) = (await session.execute(select(ClipRecord.output_path))).scalars()
    assert unicodedata.normalize("NFC", Path(path).name) == enhanced
    assert (folder / "04_Alizé.mp4").exists()


async def test_a_manifest_that_cannot_be_read_is_reported_and_the_rest_still_maps(inbox, db):
    factory, _ = db
    broken, fine = _job_id(), _job_id()
    _make_job_folder(inbox, broken, [{"filename": "00_A.mp4", "title": "A"}])
    (inbox / broken / "manifest.csv").write_bytes(b"filename,title\n00_A.mp4,\xff\xfe not utf-8\n")
    _make_job_folder(inbox, fine, [{"filename": "00_B.mp4", "title": "B"}])

    report = await mapper.map_inbox(factory, inbox, apply=True)

    assert (report.imported_jobs, report.imported_clips) == (1, 1)
    assert any(broken[:8] in r and "unreadable" in r for r in report.skipped), report.skipped


def test_a_symlinked_mp4_is_never_listed_or_served(inbox, tmp_path):
    job = _job_id()
    folder = _make_job_folder(inbox, job, [{"filename": "00_Link.mp4", "title": "Link"}], with_files=False)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not ours")
    (folder / "00_Link.mp4").symlink_to(outside)

    (found,) = mapper.read_inbox(inbox)

    assert found.clips == () and found.mp4s == {}
    assert any("not in the folder" in reason for reason in found.skipped)


async def test_a_live_clip_with_no_file_anywhere_is_reported_and_a_retired_one_is_not(inbox, db):
    factory, _ = db
    job = _job_id()
    _make_job_folder(inbox, job, [{"filename": "00_Here.mp4", "title": "Here"}])
    async with factory() as session:
        session.add(JobRecord(id=job, youtube_url="https://yt.test/x", status="completed"))
        await session.flush()
        session.add(ClipRecord(job_id=job, title="Live", output_path="/tmp/yt/gone/07_Live.mp4"))
        session.add(ClipRecord(job_id=job, title="NoPath"))
        session.add(ClipRecord(job_id=job, title="Old", output_path="/tmp/yt/gone/08_Old.mp4", retired=True))
        await session.commit()

    report = await mapper.map_inbox(factory, inbox, apply=True)

    reported = " | ".join(report.skipped)
    assert "07_Live.mp4" in reported and "no file recorded" in reported
    assert "08_Old.mp4" not in reported


async def test_an_inbox_job_whose_stored_state_is_not_a_dict_is_still_titled(inbox, db):
    factory, new_store = db
    job = _job_id()
    _make_job_folder(inbox, job, [{"filename": "00_A.mp4", "title": "A"}])
    async with factory() as session:
        session.add(JobRecord(id=job, youtube_url=f"inbox://{job}", status="completed", state=["oops"]))
        await session.commit()

    report = await mapper.map_inbox(factory, inbox, apply=True)

    assert report.retitled == 1
    assert (await new_store().get(job)).title == f"Inbox export {job[:8]}"
