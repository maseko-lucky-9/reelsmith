"""``scripts/backfill_job_video_path.py`` (T028 b).

Jobs created before migration ``o3p4q5r6s7t8`` have ``jobs.video_path``
NULL, so their clips can never be re-rendered. The script recovers the
source from the job folder its clips live in (or from an ``upload://`` URL),
dry-run by default, writing only with ``--apply`` and never over a value.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import models as _models  # noqa: F401 — registers tables on Base
from app.db.base import Base
from app.db.models import ClipRecord, JobRecord
from scripts import backfill_job_video_path as backfill

YT = "https://www.youtube.com/watch?v="


class _World:
    """A tmp SQLite DB plus a download tree on disk."""

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

    def job_folder(self, name: str, videos: list[str]) -> Path:
        folder = self.root / name
        (folder / "clips").mkdir(parents=True)
        (folder / "exports").mkdir()
        # Rendered clips and exports are videos too, but never the source.
        (folder / "clips" / "00_Intro.mp4").write_bytes(b"clip")
        (folder / "exports" / "00_Intro.mp4").write_bytes(b"export")
        (folder / "notes.txt").write_text("not a video")
        for video in videos:
            (folder / video).write_bytes(b"source")
        return folder

    def add_job(
        self,
        job_id: str,
        url: str,
        *,
        clip_folders: list[Path] = (),
        status: str = "completed",
        video_path: str | None = None,
        retired_folders: list[Path] = (),
    ) -> None:
        async def _add() -> None:
            engine = create_async_engine(self.url)
            async with engine.begin() as conn:
                await conn.execute(
                    JobRecord.__table__.insert().values(
                        id=job_id,
                        youtube_url=url,
                        status=status,
                        video_path=video_path,
                        pipeline_options={},
                    )
                )
                for i, folder in enumerate([*clip_folders, *retired_folders]):
                    await conn.execute(
                        ClipRecord.__table__.insert().values(
                            id=f"{job_id}-c{i}",
                            job_id=job_id,
                            output_path=str(folder / "clips" / f"{i:02d}_Intro.mp4"),
                            retired=folder in retired_folders,
                        )
                    )
            await engine.dispose()

        asyncio.run(_add())

    def jobs(self) -> dict[str, tuple[Any, ...]]:
        """Every job row as (video_path, status, updated_at)."""

        async def _read() -> dict[str, tuple[Any, ...]]:
            engine = create_async_engine(self.url)
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(
                        select(
                            JobRecord.id,
                            JobRecord.video_path,
                            JobRecord.status,
                            JobRecord.updated_at,
                        )
                    )
                ).all()
            await engine.dispose()
            return {r[0]: tuple(r[1:]) for r in rows}

        return asyncio.run(_read())

    def video_path(self, job_id: str) -> str | None:
        return self.jobs()[job_id][0]

    def run(self, *extra: str) -> int:
        return backfill.main(["--database-url", self.url, *extra])


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_single_top_level_video_is_applied(world, capsys):
    folder = world.job_folder("my_video-job1aaaa", ["My Video.mp4"])
    world.add_job("job1", YT + "a", clip_folders=[folder])
    _, status, updated_at = world.jobs()["job1"]

    assert world.run("--apply") == 0

    assert world.jobs()["job1"] == (str(folder / "My Video.mp4"), status, updated_at)
    out = capsys.readouterr().out
    assert str(folder / "My Video.mp4") in out
    assert "written 1" in out


def test_old_style_folder_without_job_suffix_matches(world):
    folder = world.job_folder("my_video", ["My Video.webm"])
    world.add_job("job1", YT + "a", clip_folders=[folder])

    world.run("--apply")

    assert world.video_path("job1") == str(folder / "My Video.webm")


@pytest.mark.parametrize("ext", ["mp4", "mkv", "webm", "mov", "m4v", "MP4"])
def test_every_video_extension_is_recognised(world, ext):
    folder = world.job_folder("v", [f"source.{ext}"])
    world.add_job("job1", YT + "a", clip_folders=[folder])

    world.run("--apply")

    assert world.video_path("job1") == str(folder / f"source.{ext}")


def test_several_videos_are_ambiguous_and_skipped(world, capsys):
    folder = world.job_folder("v", ["a.mp4", "b.mkv"])
    world.add_job("job1", YT + "a", clip_folders=[folder])

    world.run("--apply")

    assert world.video_path("job1") is None
    out = capsys.readouterr().out
    assert "skipped" in out and "2 video files" in out


def test_no_video_in_folder_is_skipped(world, capsys):
    folder = world.job_folder("v", [])
    world.add_job("job1", YT + "a", clip_folders=[folder])

    world.run("--apply")

    assert world.video_path("job1") is None
    assert "no video file" in capsys.readouterr().out


def test_missing_job_folder_is_skipped(world, capsys):
    gone = world.root / "deleted_from_tmp"
    world.add_job("job1", YT + "a", clip_folders=[gone])

    world.run("--apply")

    assert world.video_path("job1") is None
    assert "job folder missing" in capsys.readouterr().out


def test_job_without_live_clips_is_skipped(world, capsys):
    folder = world.job_folder("v", ["a.mp4"])
    world.add_job("job1", YT + "a", retired_folders=[folder])

    world.run("--apply")

    assert world.video_path("job1") is None
    assert "no live clip" in capsys.readouterr().out


def test_clips_in_two_folders_are_skipped(world, capsys):
    one = world.job_folder("one", ["a.mp4"])
    two = world.job_folder("two", ["b.mp4"])
    world.add_job("job1", YT + "a", clip_folders=[one, two])

    world.run("--apply")

    assert world.video_path("job1") is None
    assert "2 job folders" in capsys.readouterr().out


def test_upload_url_uses_the_uploaded_file(world):
    upload = world.root / "uploads" / "abc.mp4"
    upload.parent.mkdir()
    upload.write_bytes(b"source")
    world.add_job("job1", f"upload://{upload}")

    world.run("--apply")

    assert world.video_path("job1") == str(upload)


def test_upload_url_with_missing_file_is_skipped(world, capsys):
    folder = world.job_folder("upload_video", ["other.mp4"])
    missing = world.root / "uploads" / "gone.mp4"
    world.add_job("job1", f"upload://{missing}", clip_folders=[folder])

    world.run("--apply")

    assert world.video_path("job1") is None
    assert "upload file missing" in capsys.readouterr().out


def test_dry_run_is_the_default_and_writes_nothing(world, capsys):
    folder = world.job_folder("v", ["a.mp4"])
    world.add_job("job1", YT + "a", clip_folders=[folder])
    before = world.jobs()

    assert world.run() == 0

    assert world.jobs() == before
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert str(folder / "a.mp4") in out


def test_non_null_video_path_is_never_overwritten(world):
    folder = world.job_folder("v", ["a.mp4"])
    world.add_job("job1", YT + "a", clip_folders=[folder], video_path="/kept.mp4")
    before = world.jobs()

    world.run("--apply")

    assert world.jobs() == before


def test_only_completed_jobs_are_considered(world):
    folder = world.job_folder("v", ["a.mp4"])
    for status in ("pending", "running", "failed"):
        world.add_job(status, YT + status, clip_folders=[folder], status=status)
    before = world.jobs()

    world.run("--apply")

    assert world.jobs() == before


def test_second_apply_is_a_no_op(world, capsys):
    folder = world.job_folder("v", ["a.mp4"])
    world.add_job("job1", YT + "a", clip_folders=[folder])
    world.run("--apply")
    after_first = world.jobs()
    capsys.readouterr()

    world.run("--apply")

    assert world.jobs() == after_first
    assert "0 job(s) with no video_path" in capsys.readouterr().out


def test_database_url_defaults_to_app_settings(world, monkeypatch):
    folder = world.job_folder("v", ["a.mp4"])
    world.add_job("job1", YT + "a", clip_folders=[folder])
    monkeypatch.setattr(backfill.settings, "db_url", world.url)

    backfill.main(["--apply"])

    assert world.video_path("job1") == str(folder / "a.mp4")
