from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

COLUMNS = [
    "filename", "title", "duration_seconds", "file_size_mb",
    "description", "hashtags", "export_path", "thumbnail_path", "job_id",
    "broll_credits", "source", "segment_origin", "source_url",
]

# How a clip's segment was produced (``clips.segment_origin``). Only these are
# written; anything else (None, a stale value) is an empty cell: unknown.
ORIGIN_CHAPTERS = "chapters"  # cut from the source's own chapter list
ORIGIN_DISCOVERED = "discovered"  # found by Reelsmith's segment proposer
ORIGIN_FULL_VIDEO = "full_video"  # the single "Full Video" pseudo-chapter
SEGMENT_ORIGINS = frozenset({ORIGIN_CHAPTERS, ORIGIN_DISCOVERED, ORIGIN_FULL_VIDEO})


def clean_segment_origin(value: object) -> str:
    """``value`` if it is a known segment origin, else ``""`` (unknown)."""
    return value if isinstance(value, str) and value in SEGMENT_ORIGINS else ""


def broll_credits(assets: list[dict] | None) -> list[dict[str, str]]:
    """One ``{provider, author, source_url}`` credit per distinct B-roll asset
    of a clip (``clips.broll_assets``), in insert order. Pexels asks API users
    to credit the videographer and link the video's Pexels page."""
    credits: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for asset in assets or []:
        key = (str(asset.get("provider", "")), str(asset.get("asset_id", "")))
        if key in seen:
            continue
        seen.add(key)
        credits.append(
            {
                "provider": key[0],
                "author": str(asset.get("author") or ""),
                "source_url": str(asset.get("source_url") or ""),
            }
        )
    return credits


def _duration(clip: dict) -> float:
    """The clip's reel length: the one recorded when an edited reel was
    rendered (``clip_words.read_reel_duration``, it differs from the clip's
    window), else ``end - start``."""
    from app.services import clip_words

    recorded = clip_words.read_reel_duration(clip.get("output_path"))
    if recorded is not None:
        return recorded
    return (clip.get("end") or 0) - (clip.get("start") or 0)


def write_manifest(
    clips: list[dict],
    export_dir: str,
    source: str | None = None,
    source_url: str | None = None,
) -> str:
    """Write ``manifest.csv`` into ``export_dir`` and return its path.

    ``source`` is the job's platform id (``jobs.source``: youtube, tiktok,
    upload, generate...), written on every row; ``None`` (a legacy job) is
    written as an empty cell. Consumers read columns by header name, so new
    columns are only ever appended.

    ``source_url`` is the job's original URL (``jobs.youtube_url``, i.e.
    ``JobState.url``; ``upload://<absolute path>/uploads/<uuid>.mp4`` for an
    upload), written as is on every row. Each row's
    ``segment_origin`` is its clip's ``segment_origin``: ``chapters``,
    ``discovered`` or ``full_video``; empty when unknown (a clip stored before
    the column existed, a reprompt over an explicit time range).
    """
    out_path = str(Path(export_dir) / "manifest.csv")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for clip in clips:
            export_path = clip.get("export_path", "")
            size_mb = ""
            if export_path and os.path.exists(export_path):
                size_mb = round(os.path.getsize(export_path) / 1_048_576, 2)
            row = {
                "filename": Path(export_path).name if export_path else "",
                "title": clip.get("title", ""),
                # An edited reel's recorded length, else its window's.
                "duration_seconds": _duration(clip),
                "file_size_mb": size_mb,
                "description": clip.get("summary", ""),
                "hashtags": json.dumps(clip.get("hashtags") or []),
                "export_path": export_path,
                "thumbnail_path": clip.get("thumbnail_path", ""),
                "job_id": clip.get("job_id", ""),
                "broll_credits": json.dumps(broll_credits(clip.get("broll_assets"))),
                "source": source or "",
                "segment_origin": clean_segment_origin(clip.get("segment_origin")),
                "source_url": source_url or "",
            }
            writer.writerow(row)
    os.replace(tmp_path, out_path)
    log.info("Manifest written to %s (%d rows)", out_path, len(clips))
    return out_path
