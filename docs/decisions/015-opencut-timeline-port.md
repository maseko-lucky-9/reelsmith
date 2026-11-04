# ADR-015: OpenCut Timeline as the Clip Editor (Port, Clean-Source Preview)

**Status:** Accepted (implemented on `feat/017-opencut-timeline-editor`; PR TBD, browser check T157 open)
**Date:** 2026-11-04
**Author:** Thulani Maseko
**Implements:** [spec 017](../../specs/017-opencut-timeline-editor/spec.md) · **Builds on:** [ADR-014](014-timeline-schema-v2.md) (timeline schema v2), [spec 016](../../specs/016-editor-render/spec.md)

ADR-015 is the next free number (the last file is ADR-014). Spec numbers and ADR numbers are separate sequences.

## Context

Spec 016 made the editor's schema v2 edits render on the server, but the editor's timeline (`web/src/components/editor/MultiTrackTimeline.tsx`) is a row of static bars that deletes an item on click, segments are trimmed by typing source seconds, and the preview plays the rendered clip, whose captions are burned in. The owner decided (plan "Phase 4b") to use OpenCut's browser timeline as the editing surface. The local OpenCut checkout (fork `maseko-lucky-9/OpenCut`, commit `3037a7e`, July 2025, MIT) has a mature timeline (selection, drag, edge resize, zoom, split, snapshot undo/redo) but no captions, export or keyframes, and is a Next.js 15 / React 18 / Radix / Tailwind 3 app that stores projects in IndexedDB/OPFS and transcodes with ffmpeg.wasm. Reelsmith's web app is Vite 8 / React 19 / Base UI / Tailwind 4 / TanStack, and the server already owns media and rendering (ADR-004).

## Decision

1. **Port, file by file, into `web/src/editor/opencut/`.** Copy what is self-contained (types, constants, time helpers, the playback store, the selection box and zoom hooks); adapt what touches OpenCut's media, project and storage stores, Radix, Tailwind 3 or Next (the timeline store, the timeline, track, element and playhead components, the resize and playhead hooks, the preview panel with its video sync, the text properties panel); skip the media panel, browser storage, ffmpeg.wasm, the waveform component, and OpenCut's unused `timeline-toolbar.tsx`. The full inventory with line counts is research R2 of spec 017. Each taken file keeps an origin header; the MIT notice sits in `web/src/editor/LICENSE-OpenCut`; `VENDOR.md` records the revision and every local change.
2. **Keep zustand.** The ported components read their stores directly; `zustand` 5.x is the one new runtime dependency.
3. **Base UI, not Radix.** Context menu, menu, tooltip, scroll area and slider come from `@base-ui/react`, which the app already uses; Tailwind 3 class names are renamed for v4.
4. **Schema v2 stays the only stored form.** `web/src/editor/adapter.ts` maps an edit to OpenCut tracks and back. Each element carries the v2 item it came from (`origin`) so an unchanged edit round-trips byte for byte; values the user changes are rounded to milliseconds. A segment is an OpenCut media element spanning the whole source, trimmed to its window, so OpenCut's edge resize means trim inward and extend outward within the source. The main track is rippled (segments back to back, in source order).
5. **Segment edits reuse the 016 rules.** Split, trim, extend and delete of a segment go through `web/src/lib/timelineEdit.ts`, so captions, overlays and voice-overs follow the source exactly as in spec 016.
6. **Clean-source preview.** New `GET /jobs/{job_id}/source` streams the job's recorded source with the clip route's HTTP range handling (extracted into one helper, which also gains a 416 for unsatisfiable ranges), the same path rule as retention (`is_managed_file`: no unsafe spelling, below a managed root, a regular file) and the app-level auth. The preview plays it through the segments with one `<video>`, seeking at segment boundaries, and draws captions and text overlays as DOM layers in the render's layout (`reel_geometry`), the render's default font file (`Anton-Regular.ttf`, copied into `web/public/fonts/` with a hash test) and the caption looks of `caption_style_for_preset` (mirrored in JSON with a test). The render is the truth.
7. **016 guarantees kept.** The rendered-cut comparison (1 ms per boundary, `[]` unknown, null = the clip's window) still gates split wherever the playhead is the rendered file's clock (the rendered-clip view and the fallback when the source is gone); the clean-source view's playhead is the edit's own clock. Re-render lock, live events, voice-over flow and save-in-flight behaviour are unchanged.
8. **Replace, then delete.** `MultiTrackTimeline.tsx` and `useTimelineEditor.ts` (with their tests) are deleted once the new editor passes the 016 route tests with unchanged expected outcomes.

## Alternatives considered

- **Grow `MultiTrackTimeline`.** Rejected by the owner: it would rebuild selection, drag, resize, zoom and undo that OpenCut already has.
- **Embed OpenCut whole (iframe or a Next.js sub-app).** Rejected: two frameworks, OpenCut's project model and storage, and no access to Reelsmith's schema v2, events and auth.
- **Rewrite the stores without zustand.** Rejected: touches every ported component for no user benefit.
- **Store OpenCut's track model on the server.** Rejected: ADR-014's schema v2 is what the render validates; a second stored shape would need a second validator.
- **Preview with ffmpeg.wasm or Media Source Extensions.** Rejected: browser transcoding contradicts ADR-004's single server render; MSE needs fragmented MP4 from the server.
- **Serve the caption font from the API (`settings.font_path`).** Deferred: follows a custom font but adds a route; the copy plus a hash test covers the default font.
- **Gate split in every view.** Rejected: the gate exists because the 016 preview played the rendered file; in the clean-source view the playhead is the edit's clock, and gating it would make the preview useless for cutting.

## Consequences

- The editor gets a real timeline and sees caption, style, overlay and cut changes before rendering.
- About 5,600 lines of third-party code enter `web/src/editor/opencut/` (before the media and storage paths are cut out); upgrading them is a manual re-port against a pinned revision (`VENDOR.md`).
- The preview can drift from the render (line wrapping, stroke, reframe crop, B-roll, blurred background, ducking); the render is the truth, and fonts and caption looks are test-guarded.
- Voice-overs are silent in the edit preview and drawn as plain bars until an audio route exists (deferred T160).
- Captions are not retimed on the timeline (deferred T161); segments cannot be reordered (schema v2).
- The rendered-clip route answers 416 instead of a broken 206 for a range past the file.

## Licensing and operations

- **OpenCut is MIT** ("Copyright 2025 OpenCut", `LICENSE` at the checkout root, read 2026-11-04). The notice is kept in `web/src/editor/LICENSE-OpenCut` and every taken file names its origin; MIT permits modification and redistribution with the notice.
- **Anton is OFL-1.1** (`app/assets/fonts/OFL.txt`); the web copy ships with the same `OFL.txt`.
- **No new service, setting or migration.** The source route is read-only and needs no configuration; the browser does no media work.
