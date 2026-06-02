"""Local-file adapter for videos uploaded directly via POST /uploads.

Handles the ``upload://`` URL scheme. No download needed — the file is
already on disk. Duration and title are probed from the file itself.

Security: the file path embedded in upload:// is validated to be strictly
inside the configured uploads root and restricted to video extensions,
preventing path traversal / arbitrary file read.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from app.services.platforms.base import Chapter, DownloadResult

# Must match the directory used by app/routers/uploads.py.
# Resolved once at import time so symlink games can't move the goalposts.
_UPLOAD_ROOT = Path("/tmp/yt/uploads").resolve()
_ALLOWED_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


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
        parsed = urlparse(url)
        raw_path = parsed.path  # e.g. /tmp/yt/uploads/uuid.mp4

        # Resolve to absolute path and confirm it stays inside _UPLOAD_ROOT.
        # Path.resolve() follows symlinks, neutralising ../.. traversal.
        candidate = Path(raw_path).resolve()
        try:
            candidate.relative_to(_UPLOAD_ROOT)
        except ValueError:
            raise PermissionError(
                f"upload path escapes uploads directory: {raw_path!r}"
            )

        # Restrict to expected video extensions.
        if candidate.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise PermissionError(
                f"upload extension not allowed: {candidate.suffix!r}"
            )

        file_path = str(candidate)
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
