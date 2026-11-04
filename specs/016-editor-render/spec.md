# Feature Specification: The clip editor's edits render

**Feature**: `016-editor-render` | **Date**: 2026-11-04 | **Plan**: [plan.md](plan.md) | **Decision**: [ADR-014](../../docs/decisions/014-timeline-schema-v2.md)

## Problem

The clip editor saves a `ClipEdit` timeline (`PUT /clips/{id}/edit`) but nothing renders it: `build_render_plan` only shaped a plan. The editor's transcript panel was always empty because a clip's word timings were not stored. The caption look was fixed, `render_clip` took one source window and only the source audio, and `voiceover_service` was wired to nothing, so the AI hook text was generated and never shown or spoken. Nine tool icons in the editor did nothing, and `CaptionTemplatePicker` carried a "Not applied" badge. The hook route also read the clip's transcript as a dict although the pipeline stores a string.

## Requirements

### Words

- **FR-001**: Every render (first render and re-render) MUST write the clip's words, on the clip's own clock, to `<job folder>/.clip_words/<clip stem>.words.json` (`clip_words.write_clip_words`), and a render that made no words MUST remove a stale sidecar. The folder sits beside `clips/` so an export never copies it and a job delete removes it.
- **FR-002**: `GET /clips/{id}/edit` MUST return `words`. A clip without a sidecar is transcribed once, from its own file, and the result cached (`clip_words.backfill_clip_words`). The backfill MUST NOT run without the clip file, for a file outside the download folder (an export copy), for a refused sidecar path, or while the clip's job is busy or being deleted; a failed or timed-out backfill answers with no words, never an error.
- **FR-003**: With no saved edit, `GET` MUST return a default timeline (`version` 0, `is_default`): the whole reel on the video track and one caption item per `caption_words_per_segment` words, each carrying its words' timings. It answers 404 when there is neither an edit nor a clip file.

### Timeline schema v2

- **FR-004**: A timeline MAY carry `"schema_version": 2` (ADR-014). A timeline without it is v1 and MUST keep loading, saving and planning as before; any other version is refused.
- **FR-005**: In v2, `video` items are source segments in source seconds. They MUST be in source order, not overlap, last at least 0.05 s, and lie inside the source range the server knows (the probed source duration, else the job's recorded duration, else the clip's own window). The edited reel is their concatenation; at most 100 segments and 20 minutes.
- **FR-006**: In v2, `caption`, `text-overlay` and `audio` items sit on the edited reel's clock and MUST NOT end past it. A caption is `{start, end, text, words[]}`: when the text has as many words as `words`, each word keeps its timing (clamped to the caption) and takes the edited spelling; otherwise the text's words are spread evenly over the caption.
- **FR-007**: An `audio` item is `{start, end, src, gain}`. `src` MUST be a bare file name with an allowed audio extension that resolves, inside the clip's own folder, to a regular file; separators, `.`/`..`, NUL bytes, absolute paths and symlinks leaving the folder are refused. `gain` is in [0, 4]. A client never supplies a path.
- **FR-008**: Top-level `caption_style` MUST be one of the six `animated_caption_service.PRESETS` keys or null; `mute_source` MUST be a boolean.
- **FR-009**: `PUT /clips/{id}/edit` MUST validate every timeline (v1 and v2) with the same checks as `build_render_plan` and answer 422 with the reason; a v1 timeline MAY still hold the editor's zero-length placeholder video item and is stored as sent.

### Render

- **FR-010**: A plan MUST render in one ffmpeg pass through `ffmpeg_tools.run` with the bundled binary (ADR-004): the source is read once over the span of its segments, each segment is cut with `trim`/`atrim`, and the pieces are concatenated in the same filter graph. `-t` is the sum of the segment lengths.
- **FR-011**: The plan's captions MUST be drawn per word by `create_subtitle_image` in the colours and size of the `caption_style` preset (no `caption_style`: the default look), not by a second `ass=` pass.
- **FR-012**: Text overlays MUST be server-drawn PNG inputs gated by `enable='between(t,S,E)'`; no client text enters the filter graph.
- **FR-013**: Each `audio` item MUST start at its reel time (`adelay`) and be mixed with `amix` into one audio stream. While one plays the source audio is ducked to the fixed level 0.25 (`VOICEOVER_DUCK_VOLUME`); with `mute_source` the source audio is dropped and a silent base keeps the audio track. A source without audio and no voice-over gives a reel without audio.
- **FR-014**: The reframe crop track MUST be computed over the span and re-based per segment (`segment_crop_track`). A plan render refuses B-roll.

### Re-render

- **FR-015**: `POST /clips/{id}/rerender` of a clip that has a saved edit MUST render that edit: the plan is validated against the job's source video, then rendered over the plan's source window and swapped in with the existing hidden-partial swap. The plan's captions are the reel's words; the sidecar is rewritten from them. A v1 edit is planned against the clip file and moved onto the source.
- **FR-016**: An edit that cannot be planned MUST be refused before anything is touched (logged; the clip, its file and the chapter status stay). Cancellation behaves as in spec 013.

### Voice-over

- **FR-017**: `POST /clips/{id}/voiceover {text?, voice?, start?}` MUST synthesise the text (default: the clip's `ai_hook_text`, 404 when it has none), write `voiceover_<n>.wav` in the clip's folder under a server-chosen name, and append an `audio` item `{start, end, src, gain: 1.0}` to the saved edit, creating a v2 edit from the default timeline when none exists. A saved v1 edit is refused (409). `end` is start plus the wav's duration, clamped to the reel's end.
- **FR-018**: `YTVIDEO_VOICEOVER_PROVIDER` is `auto` (default: Piper when the Piper command and the voice's `.onnx` and `.onnx.json` exist, else the stub), `piper`, `stub` (one second of silence) or `off` (503). Voices live in `YTVIDEO_VOICEOVER_MODEL_DIR` (default `data/models`); `python -m scripts.fetch_piper_voice` downloads one, the app never downloads at import or request time.
- **FR-019**: The text is untrusted: at most 1000 characters, cleaned by `voiceover_service.clean_text` (422); the voice name matches `[A-Za-z0-9_.-]{1,64}`; Piper runs with an argv list (no shell), the text in a temp file passed as `--input-file`, a 120 s timeout that kills the child, and a wav over 50 MB refused. A failed request leaves no wav behind.
- **FR-020**: The route answers 409 for a clip file outside the download folder, and while the clip's job is busy or being deleted (the synthesis is counted as work on the job).
- **FR-021**: The hook route (`POST /clips/{id}/ai-hook`) MUST read a transcript stored as a string (what the pipeline writes), and still the dict and list shapes of older rows.

### Editor

- **FR-022**: The editor MUST edit the transcript's caption text, pick the caption style with `CaptionTemplatePicker` (no "Not applied" badge), add and remove text overlays, add a voice-over, split and trim segments, and offer Re-render, which saves pending changes first. The nine dead tool icons are wired (Captions, Text, Voice-over, Trim) or removed.

## Threats

- **T1 (high, handled in the design).** A timeline `audio.src` or a v1 `video.src` is client text that reaches ffmpeg as an input. Now `src` is a bare file name resolved by the server inside the clip's folder (FR-007), and a v1 `src` other than the job's own source is refused when planned (FR-015). The resolved path stays server-side (`RenderPlan.to_dict` omits it).
- **T2 (medium, handled).** Overlay and caption text in a filter graph. Both are drawn to PNG by the server; the graph holds only numbers and server-built paths passed as `-i` arguments (FR-011, FR-012).
- **T3 (medium, handled).** Voice-over text as a shell or Piper argument. No shell; the text goes through a temp file, not argv; the output path is built by `reserve_output_path` (FR-019).

## Known limitations

- The even spread of a caption whose word count changed ignores speech rhythm (`ponytail:` comment in `timeline_render_service._caption_words`). Re-aligning needs forced alignment.
- A segment holding nearly all of a full crop track is thinned evenly, which can smooth over one pan step (`ponytail:` comment in `segment_crop_track`).
- The duck level 0.25 is fixed; there is no setting or UI for it.
- Deleting an `audio` item does not delete its wav; deleting the job does.
- A saved v1 edit gets no voice-over until it is opened and saved in the editor (it becomes v2).
- The real render, the browser flow and the live Piper voice download are open checks: see T133-T135.

## Out of scope

- The OpenCut timeline port (Phase 4b), transitions, waveforms, voice cloning.
- Per-item ducking levels, audio fades, and a gain control in the UI.

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001..003 | Done | `tests/unit/test_clip_words_sidecar.py`, `tests/contract/test_clip_edits_words.py` |
| FR-004..009 | Done | `tests/unit/test_timeline_schema_v2.py`, `tests/contract/test_clip_edits_v2.py` |
| FR-010..014 | Done (argv only; real render open, T133) | `tests/unit/test_render_plan.py`, `tests/e2e/test_render_plan.py` |
| FR-015, FR-016 | Done | `tests/unit/test_orchestrator_rerender.py` |
| FR-017..021 | Done (Piper with a real voice open, T135) | `tests/contract/test_voiceover_router.py`, `tests/unit/test_voiceover_clip.py`, `tests/unit/test_voiceover_retention.py` |
| FR-022 | Done (browser flow open, T134) | `web/src/lib/timelineEdit.test.ts`, `web/src/routes/clips.$clipId.edit.tools.test.tsx`, `web/src/components/editor/MultiTrackTimeline.test.tsx` |
