"""Format SSE events as human-readable log lines for the Pipeline Log panel."""
from __future__ import annotations

import json
from datetime import datetime


def format_event(event: dict, ts: str | None = None) -> str:
    """Return a single log line for one SSE event dict.

    Args:
        event: dict with keys ``type`` and ``payload``.
        ts: timestamp string ``HH:MM:SS``; defaults to ``datetime.now()``.
    """
    if ts is None:
        ts = datetime.now().strftime("%H:%M:%S")

    etype = event.get("type", "Unknown")
    payload = event.get("payload", {})
    prefix = f"[{ts}]"

    if etype == "VideoRequested":
        url = payload.get("url", "")
        return f"{prefix} 📥  Job accepted — {url}"

    if etype == "FolderCreated":
        dest = payload.get("destination_folder", "")
        return f"{prefix} 📁  Folder created — {dest}"

    if etype == "VideoDownloaded":
        title = payload.get("title", "")
        duration = float(payload.get("duration") or 0)
        return f'{prefix} ⬇️  Video downloaded — "{title}" ({duration:.0f}s)'

    if etype == "ChaptersDetected":
        n = len(payload.get("chapters") or [])
        return f"{prefix} 📋  Chapters detected — {n} chapter(s)"

    if etype == "ChapterClipExtracted":
        idx = int(payload.get("chapter_index", 0)) + 1
        clip = payload.get("clip_path", "")
        return f"{prefix} ✂️  Chapter {idx} clip extracted — {clip}"

    if etype == "ChapterTranscribed":
        idx = int(payload.get("chapter_index", 0)) + 1
        text = payload.get("text") or ""
        words = len(text.split()) if text else 0
        return f"{prefix} 🎙️  Chapter {idx} transcribed — {words} words"

    if etype == "CaptionsGenerated":
        idx = int(payload.get("chapter_index", 0)) + 1
        fmt = payload.get("format", "srt")
        return f"{prefix} 💬  Chapter {idx} captions generated ({fmt})"

    if etype == "SubtitleImageRendered":
        idx = int(payload.get("chapter_index", 0)) + 1
        n = len(payload.get("image_paths") or [])
        return f"{prefix} 🖼️  Chapter {idx} subtitles rendered — {n} image(s)"

    if etype == "ClipRendered":
        idx = int(payload.get("chapter_index", 0)) + 1
        out = payload.get("output_path", "")
        return f"{prefix} ✅  Chapter {idx} clip rendered — {out}"

    if etype == "JobCompleted":
        n = len(payload.get("output_paths") or [])
        return f"{prefix} 🎉  Job completed — {n} clip(s) saved"

    if etype == "JobFailed":
        error = payload.get("error", "unknown error")
        return f"{prefix} ❌  Job failed — {error}"

    compact = json.dumps(payload, separators=(",", ":"))
    return f"{prefix} {etype} — {compact}"
