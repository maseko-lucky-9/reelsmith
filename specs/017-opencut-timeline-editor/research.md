# Research: OpenCut timeline as the clip editor

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Decision**: [ADR-015](../../docs/decisions/015-opencut-timeline-port.md)

Phase 0 of `/speckit-plan`. Each entry: Decision, Rationale, Alternatives considered. Every OpenCut fact below was read from the local checkout `~/Repo/experiments/demo/OpenCut` (remote `maseko-lucky-9/OpenCut`, a fork of OpenCut-app/OpenCut), commit `3037a7ecbf23ac8db4a3a237eecfd72af9068258` (2025-07-12, "fix: handle border"), licence MIT ("Copyright 2025 OpenCut"). Every Reelsmith fact was read from this branch (base `fcf5d07`). Technical Context had no open `NEEDS CLARIFICATION`; the items below are the choices the plan rests on.

## R1. Port OpenCut's timeline, do not grow `MultiTrackTimeline`

- **Decision**: port OpenCut's timeline (owner decision, plan "Phase 4b"), replace `web/src/components/editor/MultiTrackTimeline.tsx` (155 LOC) and `web/src/hooks/useTimelineEditor.ts` (201 LOC), delete them once green.
- **Rationale**: OpenCut already has selection, a selection box, drag and edge resize, zoom, a frame-snapped playhead, split / split-keep-left / split-keep-right, a context menu and snapshot undo/redo (`stores/timeline-store.ts` 953 LOC, `components/editor/timeline*.tsx` 2,536 LOC). The 016 component draws static bars and deletes on click.
- **Alternatives**: extend `MultiTrackTimeline` (rejected by the owner: rebuilding what OpenCut has); a commercial timeline (licence, cost).

## R2. File inventory (what is copied, adapted, skipped)

Measured with `wc -l` and the import lists of each file.

| OpenCut `apps/web/src/` | LOC | Depends on | Use |
|---|---:|---|---|
| `types/timeline.ts` | 157 | `MediaType` from `stores/media-store`, `generateUUID` from `lib/utils` | copy; two import lines swapped (a local `MediaType` union, `crypto.randomUUID`) |
| `types/playback.ts` | 21 | none | copy (the playback store's types) |
| `constants/timeline-constants.ts` | 106 | `TrackType` | copy |
| `lib/time.ts` | 25 | none | copy |
| `stores/playback-store.ts` | 134 | zustand | copy |
| `hooks/use-playback-controls.ts` | 173 | timeline store, sonner `toast` | adapt: key map (R12), split gated (R15) |
| `stores/timeline-store.ts` | 953 | zustand, `storageService` (2 uses), `useMediaStore` / `useProjectStore` / `useEditorStore` (aspect ratio on first media drop, media lookups) | adapt (R4, R5) |
| `components/editor/timeline.tsx` | 1,059 | Radix wrappers (`scroll-area`, `button`, `tooltip`, `context-menu`, `select`), `useMediaStore`, `useProjectStore`, `processMediaFiles` (ffmpeg.wasm via `lib/media-processing`), a CommonJS `require()` of `timeline-constants` | adapt |
| `components/editor/timeline-track.tsx` | 962 | `useMediaStore`, `useProjectStore`, media drag-and-drop | adapt |
| `components/editor/timeline-element.tsx` | 405 | Radix `dropdown-menu`, `context-menu`, `button`; `AudioWaveform` (wavesurfer.js); `useMediaStore` | adapt |
| `components/editor/timeline-playhead.tsx` | 110 | `use-timeline-playhead` | adapt (fps) |
| `components/editor/selection-box.tsx` | 58 | none | copy |
| `hooks/use-selection-box.ts` | 199 | none | copy |
| `hooks/use-timeline-zoom.ts` | 54 | none | copy (React 19 ref type) |
| `hooks/use-timeline-playhead.ts` | 157 | `useProjectStore` (fps) | adapt |
| `hooks/use-timeline-element-resize.ts` | 164 | `useMediaStore` (media duration bound) | adapt (R5) |
| `components/editor/preview-panel.tsx` | 497 | media store, editor store, project store, `VideoPlayer`, `AudioPlayer`, `BackgroundSettings`, `FONT_CLASS_MAP` | adapt (R7) |
| `components/ui/video-player.tsx` | 131 | playback store, `playback-seek` / `playback-update` window events | adapt into the preview (R7) |
| `components/editor/properties-panel/text-properties.tsx` | 76 | `Textarea`, `FontPicker`, `Slider`, `Input`, `property-item` | adapt (v2 fields only, R9) |
| `components/editor/properties-panel/property-item.tsx` | 47 | `cn` | copy |
| `components/editor/properties-panel/index.tsx` | 109 | project store, media store, FPS select | adapt (text only) |
| `components/editor/audio-waveform.tsx` | 114 | wavesurfer.js | skip (plain bars, `ponytail:`) |
| `components/editor/timeline-toolbar.tsx` | 219 | Radix | skip: imported by nothing upstream (`timeline.tsx` has its own toolbar) |
| `components/editor/speed-control.tsx` | 45 | Radix slider | skip (no speed in schema v2) |
| `components/editor/media-panel/**` | 587 | media store, drag-drop | skip |
| `lib/storage/**` (IndexedDB, OPFS, `storage-service.ts`) | 490 | browser storage | skip |
| `lib/ffmpeg-utils.ts`, `lib/media-processing.ts` | 331 | `@ffmpeg/ffmpeg` (wasm) | skip |
| `stores/media-store.ts`, `project-store.ts`, `editor-store.ts`, `panel-store.ts` | 737 | storage, project model | skip |
| `app/editor/[project_id]/page.tsx` | 131 | Next `useParams` | skip: the TanStack route `web/src/routes/clips.$clipId.edit.tsx` is the page |

Next.js reaches the editor only through that page (`useParams` from `next/navigation`); no editor component imports `next/link` or `next/navigation`, so "Next to TanStack Router" means the page is replaced by the existing route, nothing inside the components. wavesurfer.js is imported by `audio-waveform.tsx` but absent from `apps/web/package.json` (OpenCut's own dangling import).

- **Rationale**: copy what is self-contained; adapt what touches the skipped stores, Radix or Tailwind 3; skip everything that stores, imports or transcodes media (the server owns media, FR-023).

## R3. Keep zustand

- **Decision**: add `zustand` (OpenCut pins `^5.0.2`; the implementation pins the current 5.x it installs) and keep the ported stores as zustand stores.
- **Rationale**: the timeline, track and element components read the stores directly (`useTimelineStore()`, `usePlaybackStore()`); rewriting them to React context would touch every component and lose the "copy" status of the playback store.
- **Alternatives**: React context + reducer (rewrite of 2,500+ LOC of components); TanStack Store (another API to learn, no gain).

## R4. Segment edits go through the 016 helpers

- **Decision**: the adapted store's segment actions (split, keep-left, keep-right, trim, extend, delete) convert the store to schema v2, call `web/src/lib/timelineEdit.ts` (`splitAt`, `setSegments`, `clampToReel`) and convert back. Text-overlay and voice-over moves and resizes, caption and overlay text edits, and selection stay native store actions.
- **Rationale**: FR-007 requires the exact 016 follow-the-source rules, which `timelineEdit.ts` implements and `timelineEdit.test.ts` / `timelineEdit.helpers.test.ts` already prove (captions trimmed or dropped, word timings kept, voice-overs keep their length). OpenCut's split and trim act on one element only; captions would not follow.
- **Alternatives**: re-implement remapping on OpenCut elements (two implementations of one rule; drift).

## R5. A segment is a window of the whole source

- **Decision**: each segment is an OpenCut media element with `duration` = the source duration, `trimStart` = segment start, `trimEnd` = source duration − segment end, `startTime` = the segment's place on the reel (the sum of the earlier segments). The main track is rippled: elements always sit back to back in source order; horizontal drag of a segment is disabled; edge resize maps to trim (inward) and extend (outward), bounded by the source and the neighbours' source windows.
- **Rationale**: OpenCut's resize hook already treats `trimStart`/`trimEnd` against the media duration as the bounds of an extend, which is exactly "extend within the source". Ripple keeps ADR-014's "back to back, in source order" without a separate validator.
- **Source duration**: the preview's source `<video>` metadata, else `JobState.duration`, else the furthest segment end; a later, better value re-derives `trimEnd` without a history entry. The server re-validates every save (`validate_timeline`, 422).
- **Alternatives**: elements of the segment's own length (OpenCut could no longer extend); free placement with gaps (not expressible in schema v2).

## R6. Exact round trip: elements carry their origin

- **Decision**: every element carries `origin`, the schema v2 item it came from (and every track its v2 track minus `items`). `toTimeline` writes `{...origin, <changed fields>}`; a field the user did not change is copied from `origin`, so `toTimeline(fromTimeline(t))` deep-equals `t`. Times the editor changes are rounded to milliseconds (`round3`, as `timelineEdit.ts` does), overlay positions to four decimals.
- **Rationale**: OpenCut stores `startTime` and `duration`, overlays store pixels from the canvas centre: converting `{start: 0.1, end: 0.3}` to `startTime + duration` and back gives `0.30000000000000004`, and `(0.1 − 0.5) × 1080 / 1080 + 0.5` gives `0.09999999999999998`. Recomputing would break the round trip on unchanged edits and dirty every opened clip.
- **Unknown fields**: kept in `origin` and written back unchanged (the server ignores keys it does not read).
- **Alternatives**: round everything (changes unchanged edits); a side map keyed by element id (survives fewer OpenCut operations than a field on the element: OpenCut spreads elements on update, so an extra field rides along).

## R7. Preview: one source `<video>`, seek at boundaries

- **Decision**: the preview mounts one `<video src="/api/jobs/{job_id}/source">`, adapted from OpenCut's `video-player.tsx` sync (it listens to the playback store's `playback-seek` / `playback-update` window events and corrects drift over 0.5 s). The reel time maps to a source time through the segments (`timelineEdit.sourceAt`); when playback reaches a segment's end the video seeks to the next segment's start. Captions and overlays are DOM layers above it.
- **Rationale**: OpenCut mounts one player per active media element; Reelsmith has one source, so one element suffices and keeps the browser's buffer. Seeking at a boundary is the simplest correct behaviour.
- **Alternatives**: two alternating `<video>` elements pre-seeked to the next segment (smoother, more state; can come later); Media Source Extensions (needs fragmented MP4, i.e. server transcoding); ffmpeg.wasm (FR-023).
- **Known cost**: a short stall at a boundary (spec Known limitations).

## R8. Source route

- **Decision**: `GET /jobs/{job_id}/source` in `app/routers/media.py` (a second `APIRouter(prefix="/jobs")`), reusing a range helper extracted from `stream_video` (`media.py:34-79`). Path checks: `JobState.video_path` from the store; `retention.is_managed_file(video_path)` (rejects NUL, relative and `..` spellings lexically, resolves, requires a path below `managed_roots()`); `Path.is_file()`. 404 for each failure with a fixed detail (never the path); 409 `the job is being deleted` while `orchestrator.deleting(job_id)` (the re-render route's wording); 416 with `Content-Range: bytes */<size>` when the range start is at or past the size or after the end. Media type by suffix from an allow-list (`.mp4`/`.m4v` `video/mp4`, `.mov` `video/quicktime`, `.webm` `video/webm`, `.mkv` `video/x-matroska`, else `application/octet-stream`); `Content-Disposition: inline`.
- **Rationale**: same resolution rule as the rest of the app (a database path is untrusted, retention's module docstring), same auth (the app-level `require_api_key` dependency, bearer or `?token=`), and the clip route's proven range code. The clip route today answers a start past the end with a 206 whose `Content-Length` is zero or negative (`chunk_size = end - start + 1`, `media.py:48-49`); sharing the helper fixes that (FR-003).
- **Alternatives**: `FileResponse` alone (Starlette's own range support differs by version and would make the two routes behave differently); a static mount of the download folder (exposes every file).

## R9. Properties panel shows schema v2 fields only

- **Decision**: a caption shows its text (and a note when the word count changed: the server spreads the words evenly, ADR-014); a text overlay shows text (max 500 characters, at most 10 line breaks), font size (6-400, `FONT_SIZE_RANGE`), colour and position (x, y fractions). No font picker, rotation, opacity, background, alignment, weight or decoration controls.
- **Rationale**: schema v2 has no such fields; showing them would advertise features the render ignores (precedent T013).

## R10. Same font file, same caption looks

- **Decision**: copy `app/assets/fonts/Anton-Regular.ttf` (the default `settings.font_path`) and its `OFL.txt` to `web/public/fonts/`, load it with `@font-face` as the preview's caption and overlay font, and keep a pytest drift guard comparing the two files' sha256. Mirror `subtitle_image_service.caption_style_for_preset` (font size, fill, highlight, stroke, highlights) for `None` and the six `PRESETS` keys in `web/src/editor/caption-looks.json`, checked by the same pytest against the Python values.
- **Rationale**: the plan's risk "DOM preview vs ffmpeg render drift: same font file in both; the render is the truth". A copy plus a hash test is the simplest way to ship the font with the Vite build and fail CI the day they differ.
- **Alternatives**: a `GET /fonts/caption` route serving `settings.font_path` (follows a custom font, but adds a route and a runtime dependency for a preview); `@fontsource/anton` (a different build of the font, drift by design).

## R11. Preview layout mirrors `reel_geometry`

- **Decision**: `web/src/editor/previewLayout.ts` reproduces `app/services/clip_service.reel_geometry`: canvas width `min(source_w, 1080)`, height `int(w / (9/16))`, the source scaled to the canvas width and centred (`inset_height = 2 * ((source_h * canvas_w + source_w) // (2 * source_w))`), captions anchored in the middle of the band below it (`inner_bottom + band // 2`). **Fallback (PR #96)**: when that band is shorter than `canvas_h // 10` (`MIN_BAND_DIVISOR`; an already-9:16 or taller source, whose band is zero or negative), the anchor is `int(canvas_h * 0.85)` (`FILLED_CANVAS_ANCHOR`) instead, so the caption sits over the picture's lower part rather than at or below the canvas edge. The mirror keeps Python's integer semantics: `int()` as `Math.trunc`, `//` as `Math.floor` (the inset's top is negative for a taller source). A shared fixture of cases (`web/src/editor/reel-geometry-cases.json`) is checked by both vitest and pytest against the real `reel_geometry`: landscape sources (e.g. 1920x1080 gives canvas 1080x1920, inset 608, band anchor 1592), the threshold pair 1080x1536 (band 192, middle 1824) and 1080x1540 (band 190, fallback 1632), and the portrait sources 576x1024 (anchor 870), 1080x1920 (1632) and 1080x2340 (1632, where the band middle would be 2025, off the canvas). Font sizes scale by preview width / canvas width.
- **Rationale**: captions sit where the render puts them; one fixture keeps the two implementations honest.
- **Not previewed**: the face-tracked crop (`YTVIDEO_REFRAME_PROVIDER=face_track`), the blurred background, B-roll (spec Known limitations).

## R12. Keyboard map

- **Decision**: Space play/pause; `S` split at the playhead; Delete/Backspace delete the selection; Cmd/Ctrl+Z undo; Shift+Cmd/Ctrl+Z and Cmd/Ctrl+Y redo. Ignored while focus is in an input, textarea or contenteditable (OpenCut checks only input and textarea).
- **Rationale**: OpenCut binds split to Cmd/Ctrl+S (the browser's save), keep-left to Cmd/Ctrl+Q (quits the browser on macOS), keep-right to Cmd/Ctrl+W (closes the tab, cannot be prevented) and separate-audio to Cmd/Ctrl+D (no such feature here). Keep-left/right stay in the toolbar and context menu.

## R13. Radix to Base UI

- **Decision**: the ported components use `@base-ui/react` 1.8.0 (already a dependency): `ContextMenu` (Root, Trigger, Portal, Positioner, Popup, Item, Separator), `Menu` for the element's dropdown, `Tooltip`, `ScrollArea`, `Slider`, plus a plain `textarea`. New thin wrappers in `web/src/components/ui/` in the repo's shadcn `base-nova` style (`components.json`), beside the existing `button`, `select`, `input`, `label`.
- **Rationale**: OpenCut's `components/ui/*` wrap the `radix-ui` package; Base UI ships the same primitives and is what the web app already uses (constitution: one UI stack).

## R14. Tailwind 3 to 4, React 18 to 19

- **Decision**: in the ported files rename `shadow-sm` to `shadow-xs` (1 use), bare `rounded` to `rounded-sm` (10), `bg-gradient-to-*` to `bg-linear-to-*` (1); `flex-shrink-*` (7) still exists in Tailwind 4.3.3 (checked in `node_modules/tailwindcss/dist/lib.js`) and is left. Colours use the app's CSS variables (`bg-background`, `text-muted-foreground`), which OpenCut also uses. React 19 types: `useRef<T>(null)` with an argument, `RefObject<T | null>`, `React.JSX`. The CommonJS `require("@/constants/timeline-constants")` in `timeline.tsx` becomes an import (Vite serves ES modules).
- **Rationale**: counted with grep over the ported files; the rest of the class names are unchanged in v4.

## R15. Split gating after the port

- **Decision**: keep 016's rendered-cut comparison verbatim (`sameCut`, `renderedCut`, `cutOf`, tolerance `1e-3 + 1e-9`), moved from the route into `web/src/editor/renderedCut.ts`. It gates split (and keep-left/right) whenever the playhead is the rendered file's clock: the rendered-clip view, and the fallback when the source cannot be played. In the clean-source view the playhead is computed from the edit's own segments, so the gate is open by construction.
- **Rationale**: the gate exists because the 016 preview played the rendered file; the guarantee is "never split on a playhead from another clock". The 016 review tests (`clips.$clipId.edit.review.test.tsx`) keep running in the rendered-clip view with their assertions unchanged, and a new test proves the clean-source view splits right after a trim.
- **Alternatives**: gate in every view (makes the clean preview pointless for split); drop the gate (the rendered-clip view would split at the wrong time).

## R16. Voice-overs in the preview

- **Decision**: drawn as plain bars, not played in the edit preview.
- **Rationale**: the wav lives in the clip folder and no route serves it; adding one is a second file-serving surface with ADR-014's bare-name rules. Deferred (tasks.md, `[defer]`); the rendered clip plays the mix.

## R17. Task numbering

- **Decision**: tasks start at T139. Spec 016 used T126-T138 (T136-T138 are its deferred tasks); `grep -rhoE '\bT[0-9]{3}\b' specs tasks docs | sort -u | tail -1` gives T138.
