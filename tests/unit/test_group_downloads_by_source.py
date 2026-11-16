"""``scripts/group_downloads_by_source.py`` (spec 018): old flat job folders move
into per-source folders and every database path follows."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.db.models import ChapterRecord, ClipRecord, JobRecord
from scripts import group_downloads_by_source as grp


def test_rewrite_str_matches_whole_components_only():
    assert grp.rewrite_str("/d/a", "/d/a", "/d/G/a") == "/d/G/a"
    assert grp.rewrite_str("/d/a/clips/x.mp4", "/d/a", "/d/G/a") == "/d/G/a/clips/x.mp4"
    assert grp.rewrite_str("/d/ab/x.mp4", "/d/a", "/d/G/a") == "/d/ab/x.mp4"
    assert grp.rewrite_str("elsewhere", "/d/a", "/d/G/a") == "elsewhere"


def test_rewrite_json_walks_nested_values():
    doc = {"p": "/d/a/x", "n": 3, "l": ["/d/a/y", {"q": "/d/a"}], "none": None}
    assert grp.rewrite_json(doc, "/d/a", "/d/G/a") == {
        "p": "/d/G/a/x",
        "n": 3,
        "l": ["/d/G/a/y", {"q": "/d/G/a"}],
        "none": None,
    }


def test_plan_move_decisions(tmp_path):
    base = tmp_path
    (base / "a-1").mkdir()
    (base / "TikTok Videos" / "t-2").mkdir(parents=True)
    (base / "t-3").mkdir()
    (base / "TikTok Videos" / "t-3").mkdir()

    assert grp.plan_move("j1", "youtube", str(base / "a-1"), base).target == str(
        base / "YouTube Videos" / "a-1"
    )
    assert "no group" in grp.plan_move("j2", "upload", str(base / "a-1"), base).reason
    assert "no group" in grp.plan_move("j2", None, str(base / "a-1"), base).reason
    assert "directly under" in grp.plan_move(
        "j3", "tiktok", str(base / "TikTok Videos" / "t-2"), base
    ).reason
    assert "missing" in grp.plan_move("j4", "tiktok", str(base / "gone"), base).reason
    assert "target exists" in grp.plan_move("j5", "tiktok", str(base / "t-3"), base).reason
    assert "no destination" in grp.plan_move("j6", "tiktok", None, base).reason


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "downloads"
        self.root.mkdir()
        self.url = f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}"
        asyncio.run(self._create())

    async def _create(self) -> None:
        engine = create_async_engine(self.url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    def add_job(self, job_id: str, source: str, name: str) -> Path:
        folder = self.root / name
        (folder / "clips").mkdir(parents=True)
        (folder / "clips" / "00_Full Video.mp4").write_bytes(b"clip")
        (folder / "src.mp4").write_bytes(b"src")
        clip = str(folder / "clips" / "00_Full Video.mp4")

        async def _add() -> None:
            engine = create_async_engine(self.url)
            async with engine.begin() as conn:
                await conn.execute(
                    JobRecord.__table__.insert().values(
                        id=job_id,
                        youtube_url="https://x.test/v",
                        source=source,
                        status="completed",
                        video_path=str(folder / "src.mp4"),
                        pipeline_options={},
                        state={
                            "destination_folder": str(folder),
                            "clips_folder": str(folder / "clips"),
                            "download_path": str(self.root),
                        },
                    )
                )
                await conn.execute(
                    ChapterRecord.__table__.insert().values(
                        id=f"{job_id}-ch",
                        job_id=job_id,
                        chapter_index=0,
                        artifacts={"output_path": clip, "title": "x"},
                    )
                )
                await conn.execute(
                    ClipRecord.__table__.insert().values(
                        id=f"{job_id}-c",
                        job_id=job_id,
                        output_path=clip,
                        thumbnail_path=clip.replace(".mp4", "_thumb.jpg"),
                    )
                )
            await engine.dispose()

        asyncio.run(_add())
        return folder

    def add_job_rows(self, job_id: str, source: str, folder: Path) -> None:
        """A job row whose state names an existing (shared) folder."""

        async def _add() -> None:
            engine = create_async_engine(self.url)
            async with engine.begin() as conn:
                await conn.execute(
                    JobRecord.__table__.insert().values(
                        id=job_id,
                        youtube_url="https://x.test/v",
                        source=source,
                        status="completed",
                        pipeline_options={},
                        state={"destination_folder": str(folder)},
                    )
                )
            await engine.dispose()

        asyncio.run(_add())

    def read(self, job_id: str) -> dict:
        async def _read() -> dict:
            engine = create_async_engine(self.url)
            async with engine.connect() as conn:
                job = (
                    await conn.execute(
                        select(JobRecord.video_path, JobRecord.state, JobRecord.updated_at).where(
                            JobRecord.id == job_id
                        )
                    )
                ).one()
                clip = (
                    await conn.execute(
                        select(
                            ClipRecord.output_path, ClipRecord.thumbnail_path, ClipRecord.updated_at
                        ).where(
                            ClipRecord.job_id == job_id
                        )
                    )
                ).one()
                chapter = (
                    await conn.execute(
                        select(ChapterRecord.artifacts).where(ChapterRecord.job_id == job_id)
                    )
                ).scalar_one()
            await engine.dispose()
            return {"job": job, "clip": clip, "chapter": chapter}

        return asyncio.run(_read())


def test_dry_run_changes_nothing(tmp_path):
    world = _World(tmp_path)
    folder = world.add_job("j-yt", "youtube", "Some_Title-aaaaaaaa")

    moves, moved = asyncio.run(grp.group_downloads(world.url, world.root, apply=False))

    assert moved == 0
    assert [m.target for m in moves] == [str(world.root / "YouTube Videos" / folder.name)]
    assert folder.is_dir()
    assert not (world.root / "YouTube Videos").exists()
    assert world.read("j-yt")["clip"].output_path == str(folder / "clips" / "00_Full Video.mp4")


def test_apply_moves_the_folder_and_rewrites_every_path(tmp_path):
    world = _World(tmp_path)
    folder = world.add_job("j-tt", "tiktok", "tiktok_video-bbbbbbbb")
    before = world.read("j-tt")["job"].updated_at
    clip_before = world.read("j-tt")["clip"].updated_at
    new = world.root / "TikTok Videos" / folder.name

    _, moved = asyncio.run(grp.group_downloads(world.url, world.root, apply=True))

    assert moved == 1
    assert not folder.exists()
    assert (new / "clips" / "00_Full Video.mp4").read_bytes() == b"clip"
    row = world.read("j-tt")
    assert row["job"].video_path == str(new / "src.mp4")
    assert row["job"].state["destination_folder"] == str(new)
    assert row["job"].state["clips_folder"] == str(new / "clips")
    assert row["job"].state["download_path"] == str(world.root)
    assert row["job"].updated_at == before
    assert row["clip"].updated_at == clip_before
    assert row["clip"].output_path == str(new / "clips" / "00_Full Video.mp4")
    assert row["clip"].thumbnail_path == str(new / "clips" / "00_Full Video_thumb.jpg")
    assert row["chapter"]["output_path"] == str(new / "clips" / "00_Full Video.mp4")


def test_a_second_run_is_a_no_op_and_uploads_are_left_alone(tmp_path):
    world = _World(tmp_path)
    world.add_job("j-fb", "facebook", "fb-cccccccc")
    upload = world.add_job("j-up", "upload", "upload_video-dddddddd")

    asyncio.run(grp.group_downloads(world.url, world.root, apply=True))
    moves, moved = asyncio.run(grp.group_downloads(world.url, world.root, apply=True))

    assert moved == 0
    assert all(m.target is None for m in moves)
    assert upload.is_dir()
    assert (world.root / "Facebook Videos" / "fb-cccccccc").is_dir()


def test_a_database_failure_puts_the_folder_back(tmp_path, monkeypatch):
    world = _World(tmp_path)
    folder = world.add_job("j-yt", "youtube", "t-eeeeeeee")

    async def boom(conn, move):
        raise RuntimeError("db down")

    monkeypatch.setattr(grp, "_rewrite_rows", boom)

    with pytest.raises(grp.MoveFailedError, match="db down"):
        asyncio.run(grp.group_downloads(world.url, world.root, apply=True))

    assert (folder / "clips" / "00_Full Video.mp4").is_file()
    assert not (world.root / "YouTube Videos" / folder.name).exists()


def test_jobs_sharing_one_folder_are_left_and_do_not_stop_the_run(tmp_path):
    world = _World(tmp_path)
    shared = world.add_job("j-1", "youtube", "Same_Title")
    # A second job from before per-job folders: the same folder, other rows.
    world.add_job_rows("j-2", "youtube", shared)
    other = world.add_job("j-3", "tiktok", "tt-ffffffff")

    moves, moved = asyncio.run(grp.group_downloads(world.url, world.root, apply=True))

    assert moved == 1
    assert shared.is_dir()
    assert {m.job_id: m.reason for m in moves if m.target is None} == {
        "j-1": "folder shared by several jobs",
        "j-2": "folder shared by several jobs",
    }
    assert (world.root / "TikTok Videos" / other.name).is_dir()


def test_a_run_killed_after_the_move_only_rewrites_the_rows(tmp_path):
    world = _World(tmp_path)
    folder = world.add_job("j-yt", "youtube", "t-99999999")
    new = world.root / "YouTube Videos" / folder.name
    new.parent.mkdir()
    folder.rename(new)  # the move happened, the commit did not

    moves, moved = asyncio.run(grp.group_downloads(world.url, world.root, apply=True))

    assert moved == 1
    assert moves[0].rows_only
    assert world.read("j-yt")["clip"].output_path == str(new / "clips" / "00_Full Video.mp4")
    assert world.read("j-yt")["job"].state["destination_folder"] == str(new)


def test_one_failing_job_is_reported_and_the_rest_still_move(tmp_path, monkeypatch):
    world = _World(tmp_path)
    bad = world.add_job("j-bad", "youtube", "a-11111111")
    good = world.add_job("j-ok", "tiktok", "b-22222222")
    real = grp._rewrite_rows

    async def flaky(conn, move):
        if move.job_id == "j-bad":
            raise RuntimeError("db hiccup")
        await real(conn, move)

    monkeypatch.setattr(grp, "_rewrite_rows", flaky)

    with pytest.raises(grp.MoveFailedError, match="j-bad"):
        asyncio.run(grp.group_downloads(world.url, world.root, apply=True))

    assert bad.is_dir()
    assert (world.root / "TikTok Videos" / good.name).is_dir()
