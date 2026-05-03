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
]


def write_manifest(clips: list[dict], export_dir: str) -> str:
    out_path = str(Path(export_dir) / "manifest.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
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
            }
            writer.writerow(row)
    log.info("Manifest written to %s (%d rows)", out_path, len(clips))
    return out_path
