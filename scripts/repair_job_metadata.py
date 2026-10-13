"""Repair job metadata that never made it into the database.

    python -m scripts.repair_job_metadata                        # dry run: report only
    python -m scripts.repair_job_metadata --apply                # write titles and durations
    python -m scripts.repair_job_metadata --drop-placeholders    # dry run: jobs to drop
    python -m scripts.repair_job_metadata --drop-placeholders --apply

The old store never kept a job's title or duration, so the job page says
"Processing…". Nothing is downloaded and no job is re-run: a job with a web URL
gets its title and duration from yt-dlp's metadata alone (``download=False``),
an ``upload://`` job gets its file stem, and a job imported from the export
folder gets the title in its manifest's thumbnail path, when there is one. A
title or duration that is already set is never overwritten.

``--drop-placeholders`` removes the ``inbox://`` jobs whose every clip file is a
placeholder (a test run left 1-byte files in the export folder). A file that is
merely missing does not count: an unmounted share must not look like junk.

The export folder is only read. One transaction per run; a second run changes
nothing. Take a ``pg_dump`` before ``--apply``.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import ChapterRecord, ClipRecord, JobRecord
from app.settings import settings
from scripts.map_inbox_to_db import MIN_MP4_BYTES

PLACEHOLDER_TITLE = "Inbox export "  # the title map_inbox_to_db gives a job it cannot name
_SLUG = re.compile(r"/([^/]+)-([0-9a-f]{8})/clips/")  # <download_path>/<title slug>-<job id8>/clips/...
_JOB_ID = re.compile(r"[0-9a-f]{32}")
Fetch = Callable[[str], Mapping[str, Any]]


@dataclass(slots=True)
class Report:
    patched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def fetch_from_web(url: str) -> dict[str, Any]:
    """Title and duration of a web page's video, without downloading it."""
    from yt_dlp import YoutubeDL

    # socket_timeout bounds each network read; this runs in a worker thread no asyncio timeout can stop
    with YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "socket_timeout": 10}) as ydl:
        info = ydl.extract_info(url, download=False)
    return {"title": info["title"], "duration": info.get("duration")}


def _state(record: JobRecord) -> dict[str, Any]:
    return dict(record.state) if isinstance(record.state, dict) else {}


def _needs_title(state: Mapping[str, Any]) -> bool:
    title = state.get("title")
    return not title or str(title).startswith(PLACEHOLDER_TITLE)


def _slug_title(inbox: Path | None, job_id: str) -> str | None:
    """The video title in the job's manifest (``…/<SLUG>-<id8>/clips/…``), if its id is this job's."""
    if inbox is None or not _JOB_ID.fullmatch(job_id):
        return None
    try:
        with (inbox / job_id / "manifest.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                found = _SLUG.search(row.get("thumbnail_path") or "")
                if found and found.group(2) == job_id[:8]:
                    return found.group(1).replace("_", " ").strip() or None
    except OSError, UnicodeDecodeError, csv.Error:
        return None
    return None


def _is_web(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


async def repair_metadata(factory: async_sessionmaker, *, inbox: Path | None, fetch: Fetch, apply: bool) -> Report:
    """Fill the missing title and duration of every job we can name. See the module docstring."""
    report = Report()
    async with factory() as session:
        jobs = [
            (record.id, record.youtube_url)
            for record in (await session.execute(select(JobRecord).order_by(JobRecord.created_at))).scalars()
            if _needs_title(_state(record)) or _state(record).get("duration") is None
        ]

    fetched: dict[str, Mapping[str, Any]] = {}
    failed: dict[str, str] = {}
    for url in sorted({url for _, url in jobs if _is_web(url)}):
        try:
            fetched[url] = await asyncio.to_thread(fetch, url)
        except Exception as exc:  # yt-dlp raises its own error types; whatever went wrong, the job stays as it was
            failed[url] = f"{type(exc).__name__}: {(str(exc).splitlines() or [''])[0][:120]}"

    async with factory() as session:
        for job_id, url in jobs:
            record = await session.get(JobRecord, job_id)
            state = _state(record)
            update: dict[str, Any] = {}
            if _is_web(url):
                if url not in fetched:
                    report.skipped.append(f"{job_id[:8]} {url}: {failed[url]}")
                    continue
                meta = fetched[url]
                if _needs_title(state):
                    update["title"] = meta["title"]
                if state.get("duration") is None and meta.get("duration") is not None:
                    update["duration"] = float(meta["duration"])
            elif _needs_title(state):
                scheme = urlparse(url).scheme
                title = PurePosixPath(urlparse(url).path).stem if scheme == "upload" else None
                if scheme == "inbox":
                    title = _slug_title(inbox, job_id)
                if title:
                    update["title"] = title
            if update:
                record.state = {**state, **update}
                report.patched.append(f"{job_id[:8]} {update.get('title', state.get('title'))}")
        await session.flush()
        if apply:
            await session.commit()
        else:
            await session.rollback()
    return report


def _is_placeholder(raw: str | None) -> bool:
    """A clip file that exists and is too small to be an mp4. A path that is only missing is not one."""
    if not raw:
        return False
    path = Path(raw)
    return path.is_file() and path.stat().st_size < MIN_MP4_BYTES


async def drop_placeholders(factory: async_sessionmaker, *, apply: bool) -> Report:
    """Delete the ``inbox://`` jobs that hold nothing but placeholder clips, with their chapters and clips."""
    report = Report()
    async with factory() as session:
        job_ids = (
            (await session.execute(select(JobRecord.id).where(JobRecord.youtube_url.startswith("inbox://"))))
            .scalars()
            .all()
        )
        for job_id in job_ids:
            paths = (await session.execute(select(ClipRecord.output_path).where(ClipRecord.job_id == job_id))).all()
            if paths and all(_is_placeholder(path) for (path,) in paths):
                await session.execute(delete(ClipRecord).where(ClipRecord.job_id == job_id))
                await session.execute(delete(ChapterRecord).where(ChapterRecord.job_id == job_id))
                await session.execute(delete(JobRecord).where(JobRecord.id == job_id))
                report.dropped.append(job_id[:8])
        if apply:
            await session.commit()
        else:
            await session.rollback()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--drop-placeholders", action="store_true", help="drop the placeholder inbox jobs instead")
    parser.add_argument("--inbox", type=Path, default=None, help="default: YTVIDEO_EXPORT_BASE_FOLDER (read only)")
    parser.add_argument("--db-url", default=None, help="default: YTVIDEO_DB_URL")
    args = parser.parse_args(argv)
    inbox = args.inbox or (Path(settings.export_base_folder) if settings.export_base_folder else None)
    inbox = inbox if inbox is not None and inbox.is_dir() else None

    async def run() -> Report:
        engine = create_async_engine(args.db_url or settings.db_url, hide_parameters=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            if args.drop_placeholders:
                return await drop_placeholders(factory, apply=args.apply)
            return await repair_metadata(factory, inbox=inbox, fetch=fetch_from_web, apply=args.apply)
        finally:
            await engine.dispose()

    report = asyncio.run(run())
    print(f"({'applied' if args.apply else 'dry run, nothing written'})")
    for line in report.patched:
        print(f"  titled: {line}")
    for line in report.dropped:
        print(f"  dropped placeholder job: {line}")
    for line in report.skipped:
        print(f"  skipped: {line}")
    print(f"  patched: {len(report.patched)}  skipped: {len(report.skipped)}  dropped: {len(report.dropped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
