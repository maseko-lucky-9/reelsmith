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
    "broll_credits", "source",
]


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: object) -> object:
    """A text cell a spreadsheet cannot run: one starting with ``= + - @``,
    a tab or a carriage return gets a leading ``'`` (CSV formula injection;
    titles and summaries come from model output and transcripts). Other
    values are returned unchanged."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


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


def write_manifest(
    clips: list[dict], export_dir: str, source: str | None = None
) -> str:
    """Write ``manifest.csv`` into ``export_dir`` and return its path.

    ``source`` is the job's platform id (``jobs.source``: youtube, tiktok,
    upload, generate...), written on every row; ``None`` (a legacy job) is
    written as an empty cell. Consumers read columns by header name, so new
    columns are only ever appended.
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
                "duration_seconds": (clip.get("end") or 0) - (clip.get("start") or 0),
                "file_size_mb": size_mb,
                "description": clip.get("summary", ""),
                "hashtags": json.dumps(clip.get("hashtags") or []),
                "export_path": export_path,
                "thumbnail_path": clip.get("thumbnail_path", ""),
                "job_id": clip.get("job_id", ""),
                "broll_credits": json.dumps(broll_credits(clip.get("broll_assets"))),
                "source": source or "",
            }
            writer.writerow({key: csv_safe(value) for key, value in row.items()})
    os.replace(tmp_path, out_path)
    log.info("Manifest written to %s (%d rows)", out_path, len(clips))
    return out_path
