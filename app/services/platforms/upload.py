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

from app.services import ffmpeg_tools
from app.services.platforms.base import Chapter, DownloadResult
from app.settings import settings

# Must match the directory used by app/routers/uploads.py.
# Resolved once at import time so symlink games can't move the goalposts.
_UPLOAD_ROOT = (Path(settings.default_download_path) / "uploads").resolve()
# Where uploads were stored before T031; jobs created then still point here.
_LEGACY_UPLOAD_ROOT = Path("/tmp/yt/uploads").resolve()
_ALLOWED_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _probe_duration(path: str) -> float:
    """Return video duration in seconds via PyAV, or 0 on failure."""
    try:
        return float(ffmpeg_tools.duration(path))
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
        raw_path = parsed.path  # e.g. <download_path>/uploads/uuid.mp4

        # Resolve to absolute path and confirm it stays inside an uploads root.
        # Path.resolve() follows symlinks, neutralising ../.. traversal.
        candidate = Path(raw_path).resolve()
        if not any(
            candidate.is_relative_to(root)
            for root in (_UPLOAD_ROOT, _LEGACY_UPLOAD_ROOT)
        ):
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
