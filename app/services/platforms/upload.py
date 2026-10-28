"""Local-file adapter for videos uploaded directly via POST /uploads.

Handles the ``upload://`` URL scheme. No download needed — the file is
already on disk. Duration and title are probed from the file itself.

Security: the file path embedded in upload:// is validated to be strictly
inside the configured uploads root and restricted to video extensions,
preventing path traversal / arbitrary file read.
"""
from __future__ import annotations

import os
import stat
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
# The legacy root's parent, checked with it by ``legacy_upload_root``.
_LEGACY_BASE = Path("/tmp/yt")


def legacy_upload_root() -> Path | None:
    """The resolved legacy upload root (``/tmp/yt/uploads``), or None when it
    cannot be trusted. ``/tmp`` is world-writable: anyone could plant
    ``/tmp/yt`` as a symlink to any folder and make it deletable. So both
    ``/tmp/yt`` and ``/tmp/yt/uploads`` must be real folders (``lstat``: no
    symlink), owned by this user, with no group or world write bit. Checked
    on every call, not once at import."""
    for path in (_LEGACY_BASE, _LEGACY_BASE / "uploads"):
        try:
            st = os.lstat(path)
        except OSError:
            return None
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            return None
    return (_LEGACY_BASE / "uploads").resolve()


def upload_roots() -> tuple[Path, ...]:
    """The resolved folders an uploaded file may live in, read now (``POST
    /jobs`` and a job delete; the adapter keeps its import-time roots): the
    download path's ``uploads`` and, when it can be trusted, the legacy
    root."""
    legacy = legacy_upload_root()
    uploads = (Path(settings.default_download_path) / "uploads").resolve()
    return (uploads,) if legacy is None else (uploads, legacy)


def uploaded_file(raw: str | None) -> Path | None:
    """The resolved uploaded file ``raw`` names, or None unless it lies
    strictly below an upload root (``upload_roots``), has an allowed video
    suffix and is a regular file. ``raw`` is a path or an ``upload://`` URL.
    """
    if not raw:
        return None
    if raw.startswith("upload://"):
        raw = urlparse(raw).path
    if not raw or "\x00" in raw or not os.path.isabs(raw):
        return None
    try:
        candidate = Path(raw).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    roots = upload_roots()
    if candidate in roots or not any(candidate.is_relative_to(root) for root in roots):
        return None
    if candidate.suffix.lower() not in _ALLOWED_SUFFIXES or not candidate.is_file():
        return None
    return candidate


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
