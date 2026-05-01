"""Streamlit thin client. Submits a job to the API and renders SSE progress."""
from __future__ import annotations

import os
import subprocess

import streamlit as st

from api_client import ApiClient


_API_BASE_URL = os.environ.get("YTVIDEO_API_URL", "http://127.0.0.1:8000")

# ── Event → overall-progress milestones (0.0 – 1.0) ──────────────────────────
_GLOBAL_PROGRESS: dict[str, float] = {
    "VideoRequested":   0.02,
    "FolderCreated":    0.08,
    "VideoDownloaded":  0.20,
    "ChaptersDetected": 0.25,
    # ChapterClip/Transcribe/Captions/SubtitleImage/ClipRendered fill 0.25–0.95
    "JobCompleted":     1.00,
    "JobFailed":        None,   # progress stays wherever it is
}

# Per-chapter step → fraction of that chapter's slice (0.0 – 1.0)
_CHAPTER_STEP_FRAC: dict[str, float] = {
    "ChapterClipExtracted":   0.20,
    "ChapterTranscribed":     0.40,
    "CaptionsGenerated":      0.60,
    "SubtitleImageRendered":  0.80,
    "ClipRendered":           1.00,
}

_CHAPTER_STEP_LABEL: dict[str, str] = {
    "ChapterClipExtracted":   "✂️  Clip extracted",
    "ChapterTranscribed":     "🎙️  Transcribed",
    "CaptionsGenerated":      "💬  Captions generated",
    "SubtitleImageRendered":  "🖼️  Subtitles rendered",
    "ClipRendered":           "✅  Clip rendered",
}

_GLOBAL_STEP_LABEL: dict[str, str] = {
    "VideoRequested":   "📥  Job accepted",
    "FolderCreated":    "📁  Folder created",
    "VideoDownloaded":  "⬇️  Video downloaded",
    "ChaptersDetected": "📋  Chapters detected",
    "JobCompleted":     "🎉  All done",
    "JobFailed":        "❌  Failed",
}


def _is_valid_youtube_url(url: str) -> bool:
    return url.startswith(("https://www.youtube.com/watch?v=", "https://youtu.be/"))


def _browse_folder() -> str:
    result = subprocess.run(
        ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select download folder:")'],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().rstrip("/") if result.returncode == 0 else ""


def _render_chapter_cards(
    chapters_meta: dict[int, dict],
    chapter_status: dict[int, dict],
    container,
) -> None:
    """Redraw per-chapter progress cards inside *container*."""
    with container.container():
        st.markdown("#### Chapters")
        for idx in sorted(chapters_meta.keys()):
            meta = chapters_meta[idx]
            status = chapter_status.get(idx, {"label": "⏳  Waiting…", "frac": 0.0, "done": False})
            title = meta.get("title") or f"Chapter {idx + 1}"
            dur = meta.get("end", 0) - meta.get("start", 0)
            dur_str = f"{int(dur // 60)}m {int(dur % 60)}s" if dur else ""

            col_title, col_bar, col_step = st.columns([3, 4, 3])
            with col_title:
                st.markdown(f"**{title}**")
                if dur_str:
                    st.caption(dur_str)
            with col_bar:
                st.progress(status["frac"])
            with col_step:
                st.markdown(status["label"])


def _overall_progress(
    last_global_event: str,
    chapter_status: dict[int, dict],
    num_chapters: int,
) -> tuple[float, str]:
    """Return (fraction 0-1, status text)."""
    base = _GLOBAL_PROGRESS.get(last_global_event, 0.02) or 0.02  # guard None (e.g. JobFailed)

    if num_chapters == 0 or last_global_event in ("JobCompleted", "JobFailed"):
        return float(base), _GLOBAL_STEP_LABEL.get(last_global_event, last_global_event)

    # Chapter work occupies 0.25 → 0.95
    chapter_range = 0.95 - 0.25
    total_frac = sum(s["frac"] for s in chapter_status.values())
    chapter_contrib = (total_frac / num_chapters) * chapter_range
    overall = 0.25 + chapter_contrib

    completed = sum(1 for s in chapter_status.values() if s.get("done"))
    in_progress = [s["label"] for s in chapter_status.values() if not s.get("done")]
    if completed == num_chapters:
        text = f"🎬  All {num_chapters} chapters rendered — wrapping up…"
    elif in_progress:
        text = f"{in_progress[0]}  ({completed}/{num_chapters} chapters done)"
    else:
        text = "⏳  Processing chapters…"

    return min(overall, 0.95), text


def main() -> None:
    st.set_page_config(page_title="YouTube Clipper", page_icon="🎬", layout="centered")
    st.title("🎬 YouTube Video Clipper")

    # ── Inputs ────────────────────────────────────────────────────────────────
    url = st.text_input("YouTube Video URL").strip()

    col_input, col_btn = st.columns([4, 1])
    with col_input:
        download_path = st.text_input(
            "Folder to save the video",
            value=st.session_state.get("download_path", ""),
            key="download_path_input",
        )
    with col_btn:
        st.write("")  # vertical-alignment spacer
        if st.button("Browse…"):
            chosen = _browse_folder()
            if chosen:
                st.session_state["download_path"] = chosen
                st.rerun()

    download_path = st.session_state.get("download_path", download_path)

    if not st.button("Process Video", type="primary"):
        return

    # ── Validation ────────────────────────────────────────────────────────────
    if not download_path or not os.path.isdir(download_path):
        st.error(f"The folder '{download_path}' does not exist.")
        return
    if not _is_valid_youtube_url(url):
        st.error("Invalid YouTube URL.")
        return

    # ── Submit ────────────────────────────────────────────────────────────────
    client = ApiClient(_API_BASE_URL)
    with st.spinner("Submitting job…"):
        job_id = client.create_job(url, download_path)
    st.info(f"Job `{job_id}` accepted — streaming progress…")

    # ── Progress widgets ──────────────────────────────────────────────────────
    overall_bar   = st.progress(0.02, text="📥  Job accepted…")
    global_steps  = st.empty()     # ticks off completed global milestones
    chapter_panel = st.empty()     # redrawn on every chapter event
    output_panel  = st.empty()

    completed_globals: list[str] = []
    chapters_meta: dict[int, dict] = {}
    chapter_status: dict[int, dict] = {}
    last_global = "VideoRequested"
    num_chapters = 0

    def _redraw_globals():
        with global_steps.container():
            st.markdown("#### Pipeline steps")
            all_steps = [
                "VideoRequested", "FolderCreated", "VideoDownloaded", "ChaptersDetected",
            ]
            for step in all_steps:
                done = step in completed_globals
                icon = "✅" if done else "⬜"
                label = _GLOBAL_STEP_LABEL.get(step, step)
                st.markdown(f"{icon} {label}")

    _redraw_globals()

    # ── Stream SSE ────────────────────────────────────────────────────────────
    for event in client.stream_events(job_id):
        etype   = event["type"]
        payload = event["payload"]

        # ── Global milestones ─────────────────────────────────────────────
        if etype in _GLOBAL_PROGRESS:
            last_global = etype
            if etype not in completed_globals:
                completed_globals.append(etype)

        if etype == "ChaptersDetected":
            raw_chapters = payload.get("chapters", [])
            num_chapters = len(raw_chapters)
            for ch in raw_chapters:
                idx = ch.get("index", 0)
                chapters_meta[idx] = ch
                chapter_status[idx] = {"label": "⏳  Waiting…", "frac": 0.0, "done": False}

        # ── Per-chapter events ────────────────────────────────────────────
        if etype in _CHAPTER_STEP_FRAC:
            idx  = payload.get("chapter_index", 0)
            frac = _CHAPTER_STEP_FRAC[etype]
            done = frac >= 1.0
            chapter_status[idx] = {
                "label": _CHAPTER_STEP_LABEL[etype],
                "frac":  frac,
                "done":  done,
            }

        # ── Redraw ────────────────────────────────────────────────────────
        _redraw_globals()

        if chapters_meta:
            _render_chapter_cards(chapters_meta, chapter_status, chapter_panel)

        frac, text = _overall_progress(last_global, chapter_status, num_chapters)
        overall_bar.progress(frac, text=text)

        # ── Terminal events ───────────────────────────────────────────────
        if etype == "JobCompleted":
            overall_bar.progress(1.0, text="🎉  All clips rendered!")
            final = client.get_job(job_id)
            paths = final.get("output_paths", [])
            with output_panel.container():
                st.success(f"Done! {len(paths)} clip(s) saved.")
                for path in paths:
                    st.code(path)
            return

        if etype == "JobFailed":
            final = client.get_job(job_id)
            overall_bar.progress(frac, text="❌  Job failed")
            with output_panel.container():
                st.error(f"Job failed: {final.get('error', 'unknown error')}")
            return


if __name__ == "__main__":
    main()
