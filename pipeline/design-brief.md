# Design Brief — Pipeline Log Panel

## Goal
Add a live-updating, collapsible log panel beneath the progress bar in `ui/streamlit_app.py` that renders one human-readable line per SSE event as the job runs.

## Key Design Decisions

### D1. Where the formatting logic lives
**Chosen:** Pure helper module `ui/log_formatter.py` with one function `format_event(event: dict) -> str`.
- **Why:** keeps `streamlit_app.py` focused on layout; pure function is trivially unit-testable without spinning up Streamlit; matches existing repo style (services kept small and pure).
- **Rejected:** inlining `if/elif` chain inside the SSE loop — harder to test, mixes concerns.

### D2. Streamlit widget for the panel
**Chosen:** `st.expander("Pipeline Log", expanded=False)` containing a single `st.empty()` placeholder, redrawn on every event with the full accumulated log rendered as one `st.code(text, language=None)` block.
- **Why:** `st.code` is monospace, has built-in scroll, looks acceptable in both dark and light modes (uses Streamlit's theme-aware code background), no HTML/CSS hacks. Redrawing one block is simpler than appending and avoids Streamlit re-render quirks. `st.empty().container()` is the established pattern already used by `chapter_panel` and `output_panel` in this file.
- **Rejected — `st.text_area`:** read-only mode is awkward, doesn't auto-scroll cleanly.
- **Rejected — appending individual `st.text` calls:** Streamlit re-renders the entire script on rerun; growing widget lists is brittle.
- **Rejected — `st.chat_message` per line:** semantically wrong, heavyweight.

### D3. Log line format
**Chosen:** `[HH:MM:SS] <icon> <friendly label> — <key detail>`

Examples:
```
[12:34:01] 📥  Job accepted — https://youtu.be/abc123
[12:34:02] 📁  Folder created — /Users/me/Downloads/MyVideo
[12:34:18] ⬇️  Video downloaded — "Title goes here" (342s)
[12:34:19] 📋  Chapters detected — 5 chapters
[12:34:25] ✂️  Chapter 1/5 clip extracted — Intro
[12:34:40] 🎙️  Chapter 1/5 transcribed (12.3s audio)
[12:34:42] 💬  Chapter 1/5 captions generated (87 words)
[12:34:45] 🖼️  Chapter 1/5 subtitles rendered
[12:34:55] ✅  Chapter 1/5 clip rendered — /path/to/clip_01.mp4
[12:35:02] 🎉  Job completed — 5 clip(s)
[12:35:02] ❌  Job failed — <error message>
```
- Reuses existing `_GLOBAL_STEP_LABEL` and `_CHAPTER_STEP_LABEL` icon vocabulary already in `streamlit_app.py` (consistency).
- Timestamp formatted client-side at event-arrival time (`datetime.now().strftime("%H:%M:%S")`) — backend events don't carry a wall-clock timestamp the UI can rely on, and arrival-time is what the user perceives.

### D4. Failure highlighting
**Chosen:** When `JobFailed` arrives, render the panel with a leading `st.error(...)` showing the error message, followed by the full `st.code` log. Panel auto-expands on failure (`expanded=True`).
- **Why:** matches existing UX pattern (`output_panel` already uses `st.error` for failure).

### D5. Payload field extraction
**Chosen:** Each event type has a known small set of useful fields. The formatter has a per-event-type lookup; unknown event types fall back to `[ts] <type> — <payload as compact json>`.
- **Why:** robust to backend adding new event types without breaking the UI; gives a sane default.

## Trade-offs Accepted
- Redrawing the full log on every event is O(n) per event but n is small (~5 + 5×chapters ≈ 30-50 lines); not worth optimizing.
- Wall-clock timestamps are arrival-time, not event-emission-time. If backend latency spikes, timestamps will skew slightly. Acceptable — this panel is for human progress feel, not forensic logging.
- `st.code` lacks color per line (single mono block). Failure is signalled by the `st.error` banner above, not by coloring the failed line. Simpler and theme-safe.

## Open Questions
None — all five decisions have a chosen path with rationale.

## No new dependencies
All work uses Python stdlib (`datetime`, `json`) + existing Streamlit primitives. Constraint #6 satisfied without justification needed.
