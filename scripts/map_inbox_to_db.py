"""Map the content of the export folder (the Syncthing inbox) into the database.

    python -m scripts.map_inbox_to_db            # dry run: report only
    python -m scripts.map_inbox_to_db --apply    # write

The inbox is ``<inbox>/<job_id>/<NN>_<title>.mp4`` plus a ``manifest.csv`` per
job (``YTVIDEO_EXPORT_BASE_FOLDER``). For a job the database knows, each clip
whose file is gone is re-pointed at its copy in the inbox. A folder the
database has never seen (a run on the old in-memory store, say) is imported as a
completed job with one chapter and one clip per manifest row, dated from the
manifest's modification time and labelled ``inbox://<job_id>``.

Nothing in the inbox is written: it is a synced share, and an edit there would
reach other devices. The inbox must lie outside the download folder, or the
retention janitor would own its files. Run it after the app has started once (it migrates the
schema); one transaction, and a second run changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bus.job_store import _STATE_FIELDS
from app.db.models import ChapterRecord, ClipRecord, JobRecord
from app.domain.models import ChapterArtifacts, JobState, PipelineOptions
from app.services.retention import managed_roots
from app.settings import settings


class InboxRefused(ValueError):
    """The inbox cannot be mapped safely."""


_JOB_FOLDER = re.compile(r"[0-9a-f]{32}")
_INDEX_PREFIX = re.compile(r"^(\d+)_")
# enhance_speech writes <stem>.enhanced<suffix> / <stem>.enhanced-audio<suffix>; only <stem><suffix> was exported
_ENHANCED = re.compile(r"\.enhanced(?:-audio)?(?=\.[^.]+$)")


@dataclass(frozen=True, slots=True)
class InboxClip:
    index: int
    filename: str
    title: str
    duration: float | None
    summary: str
    hashtags: list[str]
    path: Path


@dataclass(frozen=True, slots=True)
class InboxJob:
    job_id: str
    folder: Path
    exported_at: datetime
    clips: tuple[InboxClip, ...]
    mp4s: dict[str, Path]  # every mp4 in the folder, by NFC name
    skipped: tuple[str, ...]


@dataclass(slots=True)
class Report:
    repointed: int = 0
    retitled: int = 0
    imported_jobs: int = 0
    imported_clips: int = 0
    skipped: list[str] = field(default_factory=list)


def _nfc(name: str) -> str:
    """macOS may store a name decomposed (NFD) where the manifest has it composed (NFC)."""
    return unicodedata.normalize("NFC", name)


def _float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _hashtags(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(tag) for tag in value] if isinstance(value, list) else []


def _read_job(folder: Path) -> InboxJob | None:
    manifest = folder / "manifest.csv"
    if not manifest.is_file():
        return None
    exported_at = datetime.fromtimestamp(manifest.stat().st_mtime, UTC)
    # every mp4 in the folder by NFC name (a manifest row can lack its filename); a symlink is not ours to serve
    mp4s = {_nfc(p.name): p for p in sorted(folder.glob("*.mp4")) if p.is_file() and not p.is_symlink()}
    rows: list[dict[str, str]] = []
    skipped: list[str] = []
    seen: set[str] = set()
    try:
        with manifest.open(encoding="utf-8", newline="") as handle:
            for position, row in enumerate(csv.DictReader(handle)):
                filename = _nfc((row.get("filename") or "").strip())
                if not filename:
                    skipped.append(f"row {position + 1}: no filename")
                elif Path(filename).name != filename:
                    skipped.append(f"{filename!r}: not a plain file name")
                elif filename in seen:
                    skipped.append(f"{filename}: listed twice")
                elif filename not in mp4s:
                    skipped.append(f"{filename}: file is not in the folder")
                else:
                    seen.add(filename)
                    rows.append(row | {"filename": filename})
    except (UnicodeDecodeError, csv.Error) as exc:
        return InboxJob(folder.name, folder, exported_at, (), mp4s, (f"manifest.csv is unreadable: {exc}",))
    indexes = [int(m.group(1)) if (m := _INDEX_PREFIX.match(r["filename"])) else -1 for r in rows]
    if len(set(indexes)) != len(indexes) or -1 in indexes:
        indexes = list(range(len(rows)))  # chapter indexes must be unique within a job
    clips = tuple(
        InboxClip(
            index=index,
            filename=row["filename"],
            title=(row.get("title") or "").strip() or Path(row["filename"]).stem,
            duration=_float(row.get("duration_seconds")),
            summary=row.get("description") or "",
            hashtags=_hashtags(row.get("hashtags")),
            path=mp4s[row["filename"]],
        )
        for index, row in zip(indexes, rows, strict=True)
    )
    return InboxJob(folder.name, folder, exported_at, clips, mp4s, tuple(skipped))


def read_inbox(inbox: Path) -> list[InboxJob]:
    """Every job folder of the inbox that has a manifest, in folder order."""
    folders = sorted(p for p in inbox.iterdir() if p.is_dir() and _JOB_FOLDER.fullmatch(p.name))
    return [job for folder in folders if (job := _read_job(folder)) is not None]


def _label(job_id: str) -> str:
    """The job page shows the job's title, and 'Processing…' when it has none."""
    return f"Inbox export {job_id[:8]}"


def _id(job_id: str, filename: str, kind: str) -> str:
    """A stable id, so a re-run finds what a first run wrote."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"reelsmith-inbox:{job_id}/{filename}/{kind}"))


async def _repoint(session, job: InboxJob, report: Report) -> None:
    for clip in (await session.execute(select(ClipRecord).where(ClipRecord.job_id == job.job_id))).scalars():
        if clip.output_path and Path(clip.output_path).is_file():
            continue  # the recorded file still exists: not ours to change
        name = _nfc(Path(clip.output_path or "").name)
        if copy := job.mp4s.get(name) or job.mp4s.get(_nfc(_ENHANCED.sub("", name))):
            clip.output_path = str(copy)
            report.repointed += 1
        elif not clip.retired:  # a retired clip is expected to have lost its file
            report.skipped.append(
                f"{job.job_id[:8]} clip {clip.id[:8]} ({name or 'no file recorded'}): "
                "no file on disk and none in the folder"
            )


def _import(session, job: InboxJob) -> None:
    count = len(job.clips)
    url = f"inbox://{job.job_id}"
    state = JobState(
        job_id=job.job_id,
        url=url,
        title=_label(job.job_id),
        download_path=str(job.folder.parent),
        destination_folder=str(job.folder),
        clips_folder=str(job.folder),
        chapters_total=count,
        chapters_done=count,
    )
    session.add(
        JobRecord(
            id=job.job_id,
            youtube_url=url,
            status="completed",
            segment_mode="auto",
            language="en-US",
            auto_hook=True,
            pipeline_options=PipelineOptions().model_dump(),
            chapters_total=count,
            chapters_done=count,
            state=state.model_dump(mode="json", include=_STATE_FIELDS),
            created_at=job.exported_at,
            updated_at=job.exported_at,
        )
    )
    for clip in job.clips:
        chapter_id = _id(job.job_id, clip.filename, "chapter")
        end = clip.duration or 0.0
        artifacts = ChapterArtifacts(chapter_index=clip.index, status="completed", output_path=str(clip.path))
        session.add(
            ChapterRecord(
                id=chapter_id,
                job_id=job.job_id,
                chapter_index=clip.index,
                title=clip.title,
                start_time=0.0,
                end_time=end,
                artifacts=artifacts.model_dump(mode="json"),
            )
        )
        session.add(
            ClipRecord(
                id=_id(job.job_id, clip.filename, "clip"),
                job_id=job.job_id,
                chapter_id=chapter_id,
                start=0.0,
                end=end,
                output_path=str(clip.path),
                title=clip.title,
                summary=clip.summary,
                hashtags=clip.hashtags,
                caption_style="static",
                created_at=job.exported_at,
                updated_at=job.exported_at,
            )
        )


async def map_inbox(factory: async_sessionmaker, inbox: Path, *, apply: bool) -> Report:
    """Re-point the known jobs' dead clip paths and import the unknown folders.

    One transaction: committed only with ``apply``, rolled back otherwise.
    """
    inbox = inbox.resolve()
    if any(inbox.is_relative_to(root) for root in managed_roots()):
        # the janitor deletes below a managed root, and the clips would then be its to delete
        raise InboxRefused(f"{inbox} is below a folder the retention janitor manages; point the inbox elsewhere")
    report = Report()
    async with factory() as session:
        known = set((await session.execute(select(JobRecord.id))).scalars())
        for job in read_inbox(inbox):
            report.skipped += [f"{job.job_id[:8]} {reason}" for reason in job.skipped]
            if job.job_id in known:
                await _repoint(session, job, report)
                record = await session.get(JobRecord, job.job_id)
                state = record.state if isinstance(record.state, dict) else {}
                if record.youtube_url == f"inbox://{job.job_id}" and not state.get("title"):
                    record.state = {**state, "title": _label(job.job_id)}
                    report.retitled += 1
            elif job.clips:
                _import(session, job)
                report.imported_jobs += 1
                report.imported_clips += len(job.clips)
                listed = {clip.filename for clip in job.clips}
                report.skipped += [
                    f"{job.job_id[:8]} {name}: file has no manifest row" for name in job.mp4s if name not in listed
                ]
            else:
                report.skipped.append(f"{job.job_id[:8]} nothing to import: no manifest row has its file")
        await session.flush()  # a dry run must hit the same constraints --apply would
        if apply:
            await session.commit()
        else:
            await session.rollback()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--inbox", type=Path, default=None, help="default: YTVIDEO_EXPORT_BASE_FOLDER")
    parser.add_argument("--db-url", default=None, help="default: YTVIDEO_DB_URL")
    args = parser.parse_args(argv)
    inbox = args.inbox or (Path(settings.export_base_folder) if settings.export_base_folder else None)
    if inbox is None or not inbox.is_dir():
        print(f"inbox folder not found: {inbox}", file=sys.stderr)
        return 2

    async def run() -> Report:
        engine = create_async_engine(args.db_url or settings.db_url, hide_parameters=True)
        try:
            return await map_inbox(async_sessionmaker(engine, expire_on_commit=False), inbox, apply=args.apply)
        finally:
            await engine.dispose()

    try:
        report = asyncio.run(run())
    except InboxRefused as exc:
        print(exc, file=sys.stderr)
        return 2
    mode = "applied" if args.apply else "dry run, nothing written"
    print(f"inbox: {inbox}  ({mode})")
    print(f"  clips re-pointed at their inbox copy: {report.repointed}")
    print(f"  inbox jobs given a title: {report.retitled}")
    print(f"  jobs imported: {report.imported_jobs}  (clips: {report.imported_clips})")
    for reason in report.skipped:
        print(f"  skipped: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
