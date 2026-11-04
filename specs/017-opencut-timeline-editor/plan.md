# Implementation Plan: OpenCut timeline as the clip editor

**Branch**: `feat/017-opencut-timeline-editor` | **Date**: 2026-11-04 | **Spec**: [spec.md](spec.md) | **Decision**: [ADR-015](../../docs/decisions/015-opencut-timeline-port.md)

**Input**: Feature specification from `specs/017-opencut-timeline-editor/spec.md`; owner plan "Phase 4b" (decisions final).

**Artifacts**: [research.md](research.md) (Phase 0) · [data-model.md](data-model.md), [contracts/source-video.md](contracts/source-video.md), [contracts/timeline-adapter.md](contracts/timeline-adapter.md), [quickstart.md](quickstart.md) (Phase 1) · [tasks.md](tasks.md) (Phase 2)

## Summary

Port OpenCut's browser timeline (MIT, commit `3037a7e`) into `web/src/editor/` as the clip editor's editing surface over timeline schema v2 (ADR-014), with a clean-source preview served by a new `GET /jobs/{id}/source`. OpenCut's stores stay zustand stores; an adapter maps OpenCut tracks to schema v2 and back with an exact round trip; segment edits reuse the 016 helpers in `web/src/lib/timelineEdit.ts` so captions and voice-overs follow the source exactly as today. The editor route `web/src/routes/clips.$clipId.edit.tsx` keeps every 016 guarantee (rendered-cut gating, re-render lock and live events, voice-over flow, save in flight) and swaps its timeline, preview and state hook; `MultiTrackTimeline.tsx` and `useTimelineEditor.ts` are deleted once green.

## Technical Context

**Language/Version**: Python 3.14 (backend route); TypeScript 6.0.x, React 19, Vite 8 (web).

**Primary Dependencies**: FastAPI (route); `@base-ui/react` 1.8.0, Tailwind 4.3.3, TanStack Router/Query, sonner, lucide-react (existing); **new: `zustand` 5.x** (OpenCut's stores). Not added: wavesurfer.js, `@ffmpeg/*`, radix-ui, next.

**Storage**: none new. `clip_edits.timeline` (JSON) and `clips.rendered_segments` read and written as in 016; the job's source file read-only.

**Testing**: pytest (contract + unit, offline), vitest + Testing Library (jsdom), `playwright-cli` ad hoc in dark and light (no committed Playwright spec).

**Target Platform**: local macOS, Chromium and Safari (HEVC sources play there).

**Project Type**: web application (FastAPI backend `app/`, React frontend `web/`).

**Performance Goals**: a trim or split visible in the preview within 1 s (SC-002); seeking served by 206 partial responses (SC-007); timeline interaction at 60 fps for a clip of up to 100 segments and 2000 captions (the schema v2 limits).

**Constraints**: the browser never transcodes or stores media (FR-023); the render stays the server's one-pass ffmpeg (constitution III); the preview font is the render's default font file (R10).

**Scale/Scope**: one route (+1 shared helper); about 5,600 LOC of OpenCut copied or adapted (21 files, before the media and storage code inside them is cut out) and about 2,650 LOC skipped (research R2); one rewritten route file; one component and one hook (with their tests) deleted.

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design: still passes.*

| Principle | Check | Result |
|---|---|---|
| I. One configuration surface | No new setting or env var; the source path is the job record's | Pass |
| II. Orchestrator sequences; services stateless | The route reads `JobStore` and `orchestrator.deleting`; no stage, no event added | Pass |
| III. One ffmpeg | No ffmpeg.wasm, no browser transcoding; the render is unchanged | Pass |
| IV. Offline default test run | New pytest use `tmp_path` files; vitest runs in jsdom; the browser flow is a manual `playwright-cli` check, not in `pytest` | Pass |
| V. Additive migrations | No migration | Pass |
| VI. Secrets | None involved | Pass |
| VII. React dashboard is the product UI | All UI in `web/`; Streamlit untouched | Pass |
| VIII. Decisions recorded | ADR-015 (port, zustand, Base UI, source route), this spec | Pass |
| Technical constraints | React 19, Vite 8, TS 6.0.x, Tailwind v4, TanStack, Vitest kept; zustand recorded in ADR-015 | Pass |
| Workflow | `pytest` and `pnpm test && pnpm build` after every change; status tags are claims backed by tests | Pass |

## Project Structure

### Documentation (this feature)

```text
specs/017-opencut-timeline-editor/
├── spec.md
├── plan.md                    # this file
├── research.md                # Phase 0: R1-R17
├── data-model.md              # Phase 1: editor state, mapping, rendered cut, preview state
├── quickstart.md              # Phase 1: validation run guide
├── contracts/
│   ├── source-video.md        # GET /jobs/{id}/source and the shared range rules
│   └── timeline-adapter.md    # schema v2 <-> editor, the round-trip contract
├── checklists/requirements.md
└── tasks.md                   # Phase 2: T139-T161
docs/decisions/015-opencut-timeline-port.md
```

### Source Code (repository root)

```text
app/
├── main.py                              # + include media.jobs_router
└── routers/media.py                     # + range_response() shared helper, + GET /jobs/{id}/source

tests/
├── unit/test_http_range.py              # new: range parsing, 200/206/416
├── unit/test_editor_preview_assets.py   # new: font sha256, caption looks, geometry fixture vs Python
└── contract/
    ├── test_job_source_route.py         # new: contracts/source-video.md cases
    └── test_media_router.py             # + 416 case for the clip route

web/
├── package.json, pnpm-lock.yaml         # + zustand
├── public/fonts/Anton-Regular.ttf, OFL.txt   # copy of app/assets/fonts (drift-guarded)
└── src/
    ├── components/ui/                   # + context-menu, menu, tooltip, scroll-area, slider, textarea (Base UI)
    ├── editor/
    │   ├── LICENSE-OpenCut              # MIT text verbatim
    │   ├── adapter.ts                   # schema v2 <-> editor state
    │   ├── types.ts                     # role, origin, EditorState (intersections over OpenCut types)
    │   ├── renderedCut.ts               # sameCut / renderedCut / cutOf, moved from the route
    │   ├── previewClock.ts              # reel time <-> source time, next boundary
    │   ├── previewLayout.ts             # reel_geometry mirror
    │   ├── useClipEdit.ts               # load / save / adopt / applyServerState (replaces useTimelineEditor)
    │   ├── caption-looks.json           # caption_style_for_preset mirror
    │   ├── reel-geometry-cases.json     # shared vitest/pytest fixture
    │   ├── editor.css                   # @font-face "Reelsmith Caption"
    │   ├── __fixtures__/timelines.ts    # adapter corpus
    │   └── opencut/                     # every file taken from OpenCut, upstream names kept, origin header
    │       ├── types/timeline.ts, types/playback.ts                     # copy
    │       ├── constants/timeline-constants.ts, lib/time.ts             # copy
    │       ├── stores/playback-store.ts                                 # copy
    │       ├── stores/timeline-store.ts                                 # adapt
    │       ├── hooks/use-playback-controls.ts                           # adapt (key map, R12)
    │       ├── hooks/use-selection-box.ts, use-timeline-zoom.ts         # copy
    │       ├── hooks/use-timeline-playhead.ts, use-timeline-element-resize.ts   # adapt
    │       └── components/
    │           ├── timeline.tsx, timeline-track.tsx, timeline-element.tsx,
    │           │   timeline-playhead.tsx, selection-box.tsx             # adapt (selection-box: copy)
    │           ├── preview-panel.tsx                                    # adapt (+ video-player sync)
    │           └── properties-panel/index.tsx, text-properties.tsx, property-item.tsx
    ├── routes/clips.$clipId.edit.tsx    # rewritten on the new editor, 016 behaviour kept
    ├── components/editor/MultiTrackTimeline.tsx (+ test)   # deleted (T156)
    └── hooks/useTimelineEditor.ts (+ test)                 # deleted (T156)
VENDOR.md                                # + OpenCut entry
```

**Structure Decision**: the existing web application layout. Everything derived from OpenCut lives under `web/src/editor/opencut/` with OpenCut's own file names, so the vendored-code register, the licence notice and a header test (`web/src/editor/vendor.test.ts`) can find it by path; Reelsmith's glue (adapter, persistence, preview math) sits beside it in `web/src/editor/`. `EditorToolPanels.tsx` and `CaptionTemplatePicker.tsx` stay where they are and are fed through the adapter.

## Design (the HOW)

### 1. Source route (FR-001..FR-003, contract [source-video.md](contracts/source-video.md))

- `app/routers/media.py`: extract `stream_video`'s range code (`media.py:38-79`) into `range_response(path, range_header, *, media_type, disposition)` returning a `FileResponse` (200), a `StreamingResponse` (206, 64 KiB chunks) or a 416 `JSONResponse` with `Content-Range: bytes */<size>`. `stream_video` calls it with `video/mp4` and `attachment` (unchanged apart from the 416).
- `jobs_router = APIRouter(prefix="/jobs", tags=["media"])`, `GET /{job_id}/source`: `store.get(job_id)` (404 on `JobNotFoundError`); `orchestrator.deleting(job_id)` 409; `job.video_path` empty, `not retention.is_managed_file(video_path)`, or `not Path(video_path).resolve().is_file()` all 404 `source video not retained`; media type from a suffix allow-list; `inline`. Registered in `app/main.py` next to `media.router`. No new event, no setting.

### 2. Vendoring and copies (FR-022)

- `pnpm add zustand`; `web/src/editor/LICENSE-OpenCut` holds OpenCut's MIT text verbatim. Every file under `web/src/editor/opencut/` starts with `// Copied from OpenCut (MIT) apps/web/src/<path> @ 3037a7e` or `// Adapted from ...` plus a one-line list of the local changes.
- Copies: `types/timeline.ts` (imports: `MediaType` becomes a local `'image' | 'video' | 'audio'` union, `generateUUID` becomes `crypto.randomUUID()`), `types/playback.ts`, `constants/timeline-constants.ts`, `lib/time.ts`, `stores/playback-store.ts`, `hooks/use-selection-box.ts`, `hooks/use-timeline-zoom.ts` (React 19 `RefObject<HTMLDivElement | null>`), `components/selection-box.tsx`, `properties-panel/property-item.tsx`.

### 3. Adapter (FR-004, FR-005, contract [timeline-adapter.md](contracts/timeline-adapter.md))

- `fromTimeline(timeline, {sourceWindow, sourceDuration})`: v1 through `upgradeTimeline` (016); merges several video tracks; builds one `EditorTrack` per v2 track (roles `segments`, `captions`, `overlays`, `voiceovers`) and one element per item with `origin`. Segments: `duration` = source duration, `trimStart` = start, `trimEnd` = duration − end, `startTime` = running sum (R5). Overlays: pixels from the centre on a 1080x1920 canvas.
- `toTimeline(state)`: `{...origin, ...changed}` per item (data-model write-back rule), tracks in loaded order, `timelineOrigin` keys kept, `schema_version: 2`.

### 4. Store (FR-006..FR-012)

- `web/src/editor/opencut/stores/timeline-store.ts` adapted from OpenCut's 953 LOC: remove `storageService`, `useMediaStore`, `useProjectStore`, `useEditorStore`, `separateAudio`, `replaceElementMedia`, `loadProjectTimeline`, `saveProjectTimeline`, `autoSaveTimeline`. Keep tracks, sorted tracks, selection, drag state, `pushHistory`/`undo`/`redo` (snapshots of `EditorSnapshot`, capped at 50).
- Segment actions (`splitElement`, `splitAndKeepLeft`, `splitAndKeepRight` on a segment, `trimSegment`, `deleteSegment`) go through `toTimeline` → `timelineEdit.splitAt` / `setSegments` → `fromTimeline` (R4); the affected segment stays selected. Rules of FR-006 refuse with a reason string (the caller toasts it) and push no history.
- Overlay and voice-over moves/resizes are native (`updateElementStartTime`, `updateElementDuration`), clamped to [0, reel end]; captions accept `updateTextElement({content})` only; `toggleTrackMute` only on the `segments` track.
- `load(timeline, ctx)` clears history; `applyServerState` (from `useClipEdit`) pushes one history entry; `setSourceDuration` re-derives segment trims without history.

### 5. Persistence (FR-005, FR-012, FR-019, FR-020)

- `web/src/editor/useClipEdit.ts` ports `useTimelineEditor` (201 LOC) onto the store: the `['clip-edit', clipId]` query (404 = default), one-time hydrate, `version`, `savedSnapshot` of the v2 form (a v1 edit is not dirty on open), `savedIsV1`, `isDirty`, `save()` with in-flight adoption (`sentRef`/`currentRef` logic kept), `applyServerState`, `words`, `loadedTimeline`, `readOnly` reason.

### 6. Timeline components (FR-008..FR-011)

- Base UI wrappers in `web/src/components/ui/` (R13). `timeline.tsx` (1,059 LOC) keeps the ruler, zoom (`use-timeline-zoom`), scroll sync, selection box, playhead and its toolbar minus media drop, duplicate, freeze frame ("coming soon") and separate audio; the CommonJS `require` becomes an import. `timeline-track.tsx` (962) drops media drag-and-drop and `useMediaStore`, refuses cross-role moves and horizontal drag of segments. `timeline-element.tsx` (405) draws audio as a plain bar (`ponytail:` add waveforms when voice-over editing needs them), its menu offers split / keep left / keep right / delete as applicable. `use-timeline-element-resize.ts` bounds a segment edge by the source and the neighbours' source windows, an overlay by the reel; captions and voice-overs get no resize handles. `use-timeline-playhead.ts` uses a constant 30 fps instead of the project store. `use-playback-controls.ts` key map per R12.
- Tailwind 4 renames and React 19 types per R14. Split actions take a `canSplitAtPlayhead` flag from the route (FR-017) and show the reason when it is false.

### 7. Preview (FR-013..FR-016)

- `previewClock.ts`: from the v2 segments, `sourceAt(t)` (016 `timelineEdit.sourceAt`), the segment index at t, and the next boundary.
- `previewLayout.ts`: `reel_geometry` mirror (R11) and the font scale.
- `preview-panel.tsx` (adapted from 497 LOC + `ui/video-player.tsx` 131): one `<video src="/api/jobs/{job_id}/source" preload="metadata">` in a 9:16 frame, synced to the playback store events; at a boundary it seeks to the next segment's start; `muted` follows `mute_source`; `loadedmetadata` feeds `setSourceDuration`; `error` calls `onUnavailable(reason)`. Caption layer: the caption at t, words highlighted per `caption-looks.json` when the style highlights, positioned at the band anchor, font `"Reelsmith Caption"` from `editor.css`. Overlay layer: each active overlay at its x/y fractions, `font_size` scaled, colour. Text is rendered as React text nodes only.
- Result view: a second mode of the same panel showing `/api/clips/{clip_id}/video?v={videoVersion}` (016's element and cache-busting), driven by its own `<video>` clock.

### 8. Route swap (FR-017..FR-021, FR-024)

- `web/src/routes/clips.$clipId.edit.tsx`: keep the top bar (back, title, undo/redo, Save, Re-render with the in-flight status, Export), the transcript panel (`CaptionRow` writes through `updateTextElement`), the tool rail and `EditorToolPanels` (fed `timeline={toTimeline(state)}` and `onChange={(t) => store.replaceFromTimeline(t)}`, one history step), the regenerate-copy checkbox, B-roll credits, the SSE `useEffect` (`/api/jobs/{job}/events?live=true`, `ClipRendered` / `ClipRerenderFailed` for this clip), `rerenderMutation`, `voiceoverMutation`, `voiceoverErrorMessage`, `STUB_VOICEOVER_WARNING`. Replace the `<video>` with the preview panel (source / result toggle), `MultiTrackTimeline` with the timeline, `useTimelineEditor` with `useClipEdit`; mount the properties panel when an element is selected (else the active tool panel).
- Gating: `playheadOnEditClock` (data-model §2) computed with `renderedCut.ts`; passed to the timeline's split actions, the keyboard handler and `TrimPanel` (`playheadStale = !playheadOnEditClock`), and to the transcript highlight.
- Then delete `MultiTrackTimeline.tsx`, `MultiTrackTimeline.test.tsx`, `useTimelineEditor.ts`, `useTimelineEditor.test.ts`; `tsc -b` proves nothing imports them.

### 9. Tests and verification

- Backend: `tests/unit/test_http_range.py`, `tests/contract/test_job_source_route.py`, `tests/contract/test_media_router.py` (+416), `tests/unit/test_editor_preview_assets.py`.
- Web: `adapter.test.ts`, `renderedCut.test.ts`, `preview.test.ts`, `useClipEdit.test.ts`, `vendor.test.ts`, `opencut/copied.test.ts`, `opencut/stores/timeline-store.test.ts`, `opencut/hooks/timeline-hooks.test.ts`, `opencut/components/timeline.test.tsx`, `opencut/components/preview-panel.test.tsx`, `opencut/components/properties-panel.test.tsx`, `components/ui/editor-primitives.test.tsx`; the three existing route tests kept green with unchanged expected outcomes; new `routes/clips.$clipId.edit.preview.test.tsx`.
- Every new test is mutation-checked ([quickstart.md §2](quickstart.md)). `ruff check app tests scripts`, `pytest`, `cd web && pnpm test && pnpm build` after each task; the `playwright-cli` flow in dark and light at the end (T157, also covers 016's T134 steps).

## Complexity Tracking

No constitution violation. The one new runtime dependency (`zustand`) and the vendored MIT code are recorded in ADR-015 and `VENDOR.md`.
