# SPDX-License-Identifier: Apache-2.0
# Vendored from https://github.com/maseko-lucky-9/sentrysearch (sentrysearch/chunker.py)
# at commit 3694dd0cb27cc57920b2b816996ce5a22662e45c. See ../README.md and /VENDOR.md.
#
# MODIFIED for Reelsmith (Apache-2.0 section 4(b)):
#   - Kept only expected_chunk_spans, is_still_frame_chunk, preprocess_chunk and the
#     duration/ffmpeg helpers they need. Dropped chunk_video, scan_directory,
#     probe_error and the Tesla-dashcam helpers.
#   - ffmpeg is always the bundled imageio-ffmpeg binary (project rule: never a system
#     ffmpeg/ffprobe); the duration comes from ffmpeg's stderr, not ffprobe.
#   - Added render_chunk(): cut + scale + fps in ONE ffmpeg pass with an accurate seek.
#     Upstream cuts with `-c copy` first, which snaps each chunk start to the previous
#     keyframe, so the stored (start, end) can be off by seconds.
#   - Added a timeout to every ffmpeg call.
#   - is_still_frame_chunk: `-vsync vfr` -> `-fps_mode vfr` (-vsync is removed in newer
#     ffmpeg) and its temp frame dir is now removed in a `finally`, not only on success.
#   - preprocess_chunk is kept for parity with upstream; scripts/scene_index.py does not
#     call it (render_chunk replaces cut-then-preprocess).
"""Video chunking logic (spans, still-frame detection, 480p/5fps preprocessing)."""

import functools
import os
import re
import shutil
import subprocess
import tempfile

FFMPEG_TIMEOUT_S = 300


@functools.lru_cache(maxsize=1)
def _get_ffmpeg_executable() -> str:
    """Return the bundled imageio-ffmpeg binary."""
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - environment specific
        raise RuntimeError(
            "imageio-ffmpeg is not available. Recreate the sidecar venv with scripts/setup_scene_venv.sh."
        ) from exc


def _parse_duration_from_ffmpeg_output(stderr_text: str) -> float:
    """Extract duration from ffmpeg stderr line: Duration: HH:MM:SS.xx"""
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr_text)
    if not match:
        # Surface ffmpeg's own error (e.g. "No such file or directory")
        for line in stderr_text.splitlines():
            lower = line.lower()
            if "no such file" in lower:
                raise FileNotFoundError(f"Video file not found: {line.strip()}")
            if "error" in lower:
                raise RuntimeError(f"ffmpeg error: {line.strip()}")
        raise RuntimeError("Could not determine video duration from ffmpeg output.")

    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def get_video_duration(video_path: str) -> float:
    """Get the duration of a video file in seconds (parses ffmpeg's banner)."""
    result = subprocess.run(
        [_get_ffmpeg_executable(), "-i", video_path],
        capture_output=True,
        text=True,
        check=False,
        timeout=FFMPEG_TIMEOUT_S,
    )
    return _parse_duration_from_ffmpeg_output(result.stderr)


def expected_chunk_spans(
    duration: float,
    chunk_duration: int = 30,
    overlap: int = 5,
) -> list[tuple[float, float]]:
    """Return the (start, end) spans that a chunker produces for a video of the
    given duration, without actually splitting it.

    Used to compute expected chunk IDs for resume/skip logic so we can
    avoid invoking ffmpeg on already-indexed files.
    """
    if overlap >= chunk_duration:
        raise ValueError(
            f"overlap ({overlap}s) must be less than chunk_duration ({chunk_duration}s). "
            "When overlap >= chunk_duration the step between chunks is <= 0, "
            "causing an infinite loop."
        )
    if duration <= chunk_duration:
        return [(0.0, float(duration))]

    step = chunk_duration - overlap
    spans: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(start + chunk_duration, duration)
        spans.append((start, end))
        start += step
        if start + overlap >= duration:
            break
    return spans


def render_chunk(
    video_path: str,
    start: float,
    end: float,
    out_path: str,
    target_resolution: int = 480,
    target_fps: int = 5,
) -> str:
    """Cut [start, end) from *video_path* straight to a 480p/5fps H.264 chunk.

    One ffmpeg pass (seek + scale + fps + encode, no audio); the seek is
    frame-accurate because the video is re-encoded. Raises on ffmpeg failure.
    """
    subprocess.run(
        [
            _get_ffmpeg_executable(),
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            video_path,
            "-t",
            f"{end - start:.3f}",
            "-an",
            "-vf",
            f"scale=-2:{target_resolution},fps={target_fps}",
            "-c:v",
            "libx264",
            "-crf",
            "28",
            "-preset",
            "veryfast",
            out_path,
        ],
        capture_output=True,
        check=True,
        timeout=FFMPEG_TIMEOUT_S,
    )
    return out_path


def is_still_frame_chunk(
    chunk_path: str,
    threshold: float = 0.98,
    verbose: bool = False,
) -> bool:
    """Check if a video chunk contains mostly still frames.

    Extracts 3 evenly-spaced frames as JPEG and compares file sizes.
    Similar JPEG sizes indicate similar visual content (still scene).

    Args:
        chunk_path: Path to the video chunk.
        threshold: Minimum size ratio (min/max) to consider frames similar.
        verbose: Print frame sizes to stderr.

    Returns:
        True if the chunk appears to be a still/static scene.
    """
    tmp_dir = None
    try:
        ffmpeg_exe = _get_ffmpeg_executable()

        # Get total frame count: try parsing "frame=" progress line first,
        # fall back to duration * fps for ffmpeg 7+ which changed the format.
        result = subprocess.run(
            [ffmpeg_exe, "-i", chunk_path, "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=FFMPEG_TIMEOUT_S,
        )
        stderr = result.stderr
        match = re.search(r"frame=\s*(\d+)", stderr)
        if match:
            total_frames = int(match.group(1))
        else:
            # Estimate from duration and fps in stream info
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s+fps", stderr)
            dur_match = re.search(
                r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                stderr,
            )
            if not fps_match or not dur_match:
                return False
            fps = float(fps_match.group(1))
            h, m, s = dur_match.groups()
            dur = int(h) * 3600 + int(m) * 60 + float(s)
            total_frames = int(dur * fps)
        if total_frames < 3:
            return False

        f1 = total_frames // 3
        f2 = 2 * total_frames // 3

        # Extract 3 frames as JPEG
        tmp_dir = tempfile.mkdtemp(prefix="sentrysearch_still_")
        out_pattern = os.path.join(tmp_dir, "frame_%03d.jpg")

        subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-i",
                chunk_path,
                "-vf",
                f"select=eq(n\\,0)+eq(n\\,{f1})+eq(n\\,{f2})",
                "-fps_mode",
                "vfr",
                out_pattern,
            ],
            capture_output=True,
            check=True,
            timeout=FFMPEG_TIMEOUT_S,
        )

        # Collect frame file sizes
        frames = sorted(os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if f.endswith(".jpg"))
        sizes = [os.path.getsize(f) for f in frames]

        if len(sizes) < 2:
            return False

        if verbose:
            import sys

            print(
                "    [verbose] still-detect frame sizes: " + ", ".join(f"{s}B" for s in sizes),
                file=sys.stderr,
            )

        min_size = min(sizes)
        max_size = max(sizes)
        if max_size == 0:
            return False

        return min_size / max_size >= threshold

    except Exception:
        return False
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def preprocess_chunk(
    chunk_path: str,
    target_resolution: int = 480,
    target_fps: int = 5,
) -> str:
    """Downscale and reduce frame rate of a video chunk for cheaper embedding.

    Args:
        chunk_path: Path to the input video chunk.
        target_resolution: Target height in pixels (width scales to maintain aspect ratio).
        target_fps: Target frames per second.

    Returns:
        Path to the preprocessed file, or the original chunk_path on failure.
    """
    try:
        ffmpeg_exe = _get_ffmpeg_executable()
        base, ext = os.path.splitext(chunk_path)
        out_path = f"{base}_preprocessed{ext}"

        subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-i",
                chunk_path,
                "-vf",
                f"scale=-2:{target_resolution},fps={target_fps}",
                "-c:v",
                "libx264",
                "-crf",
                "28",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                out_path,
            ],
            capture_output=True,
            check=True,
            timeout=FFMPEG_TIMEOUT_S,
        )
        return out_path
    except Exception:
        return chunk_path
