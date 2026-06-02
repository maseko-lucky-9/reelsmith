"""Local-file adapter for videos uploaded directly via POST /uploads.

Handles the ``upload://`` URL scheme. No download needed — the file is
already on disk. Duration and title are probed from the file itself.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from app.services.platforms.base import Chapter, DownloadResult


def _probe_duration(path: str) -> float:
    """Return video duration in seconds via ffprobe, or 0 on failure."""
    try:
        import subprocess
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        import json
        info = json.loads(result.stdout)
        return float(info.get("format", {}).get("duration", 0))
    except Exception:
        return 0.0


class UploadAdapter:
    """Serves a local uploaded file as if it were a downloaded platform video."""

    platform_id = "upload"

    @classmethod
    def matches(cls, url: str) -> bool:
        return isinstance(url, str) and url.startswith("upload://")

    def download(self, url: str, destination_folder: str) -> DownloadResult:
        # Strip the upload:// scheme to get the real fs path
        parsed = urlparse(url)
        file_path = parsed.path  # e.g. /tmp/yt/uploads/uuid.mp4

        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Uploaded file not found: {file_path}")

        stem = Path(file_path).stem
        duration = _probe_duration(file_path)

        return DownloadResult(
            video_path=file_path,
            info={
                "title": stem,
                "duration": duration,
                "chapters": [],
                "upload_date": None,
                "description": "",
                "tags": [],
            },
            title=stem,
            duration=duration,
            source=self.platform_id,
        )

    def extract_chapters(self, info: dict) -> list[Chapter]:
        return []
