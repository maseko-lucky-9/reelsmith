---

description: "Task list for spec 017: OpenCut timeline as the clip editor"
---

# Tasks: OpenCut timeline as the clip editor

**Input**: Design documents from `specs/017-opencut-timeline-editor/`: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Prerequisites**: spec 016 merged (PR #94: schema v2, `rendered_segments`, voice-over route, `web/src/lib/timelineEdit.ts`); ADR-015.

**Tests**: requested (owner input: adapter round trip, store split/trim/undo, editor route vitest, one `playwright-cli` flow in dark and light, pytest for the new route). Every task names its proving test; every new test is mutation-checked (break the code, see it go red, restore) before the task is ticked.

**Numbering**: T139 onward. Spec 016 used T126-T138 (T136-T138 are its deferred tasks), so T139 is the next free number.

**Organization**: setup and foundational work first (backend route, adapter, stores: all testable without UI), then the timeline components (US1), the preview (US2), the route swap and deletion (US3), the browser flow, then docs.

## Format: `[ID] [P?] [Story] [FR] Description. Proving test: ...`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story (US1-US3) for story-phase tasks only
- **[FR]**: the spec requirements the task satisfies
- Paths are repository-relative: backend `app/`, `tests/`; frontend `web/src/`

## Path Conventions

- Backend: `app/routers/`, `app/main.py`; tests in `tests/unit/`, `tests/contract/`
- Frontend: Reelsmith glue in `web/src/editor/`; every file derived from OpenCut in `web/src/editor/opencut/` (OpenCut's file names kept); Base UI wrappers in `web/src/components/ui/`; the page in `web/src/routes/clips.$clipId.edit.tsx`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: the shared HTTP range handling the new route reuses.

- [x] T139 (done: commit `b5a20c4`) [P] [FR-003] Extract `range_response(path, range_header, *, media_type, disposition)` from `stream_video` in `app/routers/media.py`: no `Range` or one that does not match `^bytes=(\d+)-(\d*)$` gives 200 with the whole file and `Accept-Ranges: bytes`; `bytes=S-E` with `S < size` and `S <= E` gives 206 (E clamped to `size - 1`, missing E is `size - 1`, 64 KiB chunks, `Content-Range: bytes S-E/<size>`); `S >= size` or `S > E` gives 416 with `Content-Range: bytes */<size>`. `stream_video` uses it and keeps `video/mp4` and `attachment`. Proving test: `tests/unit/test_http_range.py` (open-ended, clamp, both 416 cases, suffix range `bytes=-500` gives 200) and a new 416 case in `tests/contract/test_media_router.py`; mutation: delete the 416 branch, both go red.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the source route, the vendored base, the adapter, the stores and persistence: everything the UI stands on, all testable without a component.

**CRITICAL**: no user-story task starts before this phase is green.

- [x] T140 (done: commit `b5a20c4`) [FR-001, FR-002] Add `GET /jobs/{job_id}/source` (`jobs_router = APIRouter(prefix="/jobs", tags=["media"])` in `app/routers/media.py`, registered in `app/main.py`) per [contracts/source-video.md](contracts/source-video.md): `store.get` (404 `job not found: <id>`), `orchestrator.deleting(job_id)` (409 `the job is being deleted`), empty `video_path`, `not retention.is_managed_file(video_path)` or not a regular file (404 `source video not retained`, path never echoed), `range_response` with the suffix media type allow-list and `inline`. Depends on T139. Proving test: `tests/contract/test_job_source_route.py`, the 11 cases of the contract (200, 206 head/tail/clamped, 416 both ways, suffix range, the 404 family incl. outside-folder, symlink-out, `..`, relative and NUL paths with no path in the body, 409, `/api` prefix, 401 / `?token=`, media types); mutation: drop the `is_managed_file` check (outside-folder and symlink cases red) and the `deleting` check (409 case red).
- [x] T141 (done: commit `f77c76d`) [P] [FR-022, FR-023] Vendored base: `pnpm add zustand` (`web/package.json`, `web/pnpm-lock.yaml`); `web/src/editor/LICENSE-OpenCut` (MIT text verbatim, "Copyright 2025 OpenCut"); copy into `web/src/editor/opencut/`, each file headed `// Copied from OpenCut (MIT) apps/web/src/<path> @ 3037a7e` plus its local changes: `types/timeline.ts` (`MediaType` becomes a local `'image' | 'video' | 'audio'` union, `generateUUID` becomes `crypto.randomUUID()`), `types/playback.ts`, `constants/timeline-constants.ts`, `lib/time.ts`, `stores/playback-store.ts`, `hooks/use-selection-box.ts`, `hooks/use-timeline-zoom.ts` (`RefObject<HTMLDivElement | null>`), `components/selection-box.tsx`, `components/properties-panel/property-item.tsx`; draft the `VENDOR.md` OpenCut entry (source, commit, location). Proving test: `web/src/editor/vendor.test.ts` (every non-test file under `web/src/editor/opencut/` starts with a `Copied from OpenCut (MIT)` or `Adapted from OpenCut (MIT)` header naming an upstream path and `3037a7e`; `LICENSE-OpenCut` holds the MIT grant; `web/package.json` lists none of `wavesurfer.js`, `@ffmpeg/ffmpeg`, `@ffmpeg/core`, `radix-ui`, `next`) and `web/src/editor/opencut/copied.test.ts` (`formatTimeCode` formats, `snapTimeToFrame` at 30 fps, `usePlaybackStore.seek` clamps to the duration and dispatches `playback-seek`); mutation: strip one header, red. `pnpm build` green.
- [x] T142 (done: commit `1499f56`) [FR-004, FR-005] Adapter `web/src/editor/adapter.ts`, extension types `web/src/editor/types.ts` (`TrackRole`, `origin`, `originTrack`, `EditorState`, by intersection over the copied OpenCut types) and corpus `web/src/editor/__fixtures__/timelines.ts`, per [contracts/timeline-adapter.md](contracts/timeline-adapter.md) and the mapping in [data-model.md §2](data-model.md): segments as windows of the whole source (`duration` = source duration, `trimStart` = start, `trimEnd` = duration − end), overlays in pixels from the centre on 1080x1920 ("`font_size` integer in [6, 400], default 36", "`x`, `y` fractions, defaults 0.5 and 0.1", "`color` default `#ffffff`"), write-back `{...origin, ...changed}` with changed times `round3` and changed positions to four decimals, v1 through `upgradeTimeline`, several video tracks merged. Depends on T141. Proving test: `web/src/editor/adapter.test.ts` (round trip deep-equal for every corpus fixture; `v1Placeholder` equals `upgradeTimeline(t, window)` and reports `converted: 'v1'`; `v1Foreign` gives `ok: false` with a reason; `twoVideoTracks` merged; changed values rounded, unchanged ones byte-equal); mutation: the four listed in the contract, each red.
- [x] T143 (done: commit `29b8120`) [P] [FR-017] Move `CUT_TOLERANCE` (`1e-3 + 1e-9`), `sameCut`, `renderedCut` and `cutOf` verbatim from `web/src/routes/clips.$clipId.edit.tsx` to `web/src/editor/renderedCut.ts`; the route imports them (no behaviour change). Proving test: `web/src/editor/renderedCut.test.ts` (boundaries 1 ms apart match, including a raw float against a 1 ms server nudge; 1.1 ms apart do not; different counts never match; `[]` is `'unknown'`; null is `[window]`; null without a window is null) and `web/src/routes/clips.$clipId.edit.review.test.tsx` unchanged and green; mutation: tolerance `1e-4`, red.
- [x] T144 (done: commit `611e98d`) [FR-006, FR-007, FR-008, FR-009, FR-012] Timeline store `web/src/editor/opencut/stores/timeline-store.ts` adapted from OpenCut (header `Adapted from`), per the store table in [data-model.md §2](data-model.md): remove `storageService`, the media/project/editor stores, `separateAudio`, `replaceElementMedia`, `loadProjectTimeline`, `saveProjectTimeline`, auto-save; snapshots of `EditorSnapshot` "capped at 50"; segment actions through `toTimeline` then `timelineEdit.splitAt` / `setSegments` then `fromTimeline` ("each segment at least 0.05 s", in source order, inside the source, never overlapping a neighbour's source window, never deleting the last one); overlay/voice-over moves clamped to [0, reel end]; captions take `content` only; `toggleTrackMute` on the segments track only; `load`, `replaceFromTimeline`, `setSourceDuration`, `readOnly`. Depends on T142. Proving test: `web/src/editor/opencut/stores/timeline-store.test.ts` (split mid-segment gives two segments and leaves captions, overlays and voice-overs where they were on the reel; trimming the head 2 s moves later captions 2 s earlier and drops captions whose speech was cut; extend stops at the source end and at the next segment's source start; each refusal changes nothing and pushes no history; segment drag and cross-role moves refused; overlay move clamped at the reel end; undo/redo restore whole snapshots including `caption_style`; a new change clears redo; 51 changes keep 50 undo steps); mutation: remove the cap, and route a segment split through OpenCut's per-element `splitElement` instead of `splitAt`, each red.
- [x] T145 (done: commit `1d54753`) [FR-005, FR-012, FR-019, FR-020] Persistence hook `web/src/editor/useClipEdit.ts` (port of `web/src/hooks/useTimelineEditor.ts` onto the store, [data-model.md §2](data-model.md)): `['clip-edit', clipId]` query with 404 as "no edit", one-time hydrate, `version`, `savedSnapshot` of the v2 form, `savedIsV1`, `isDirty`, `save()` with the in-flight adoption (`sentRef` / `currentRef`), `applyServerState` as one undo step, `words`, `loadedTimeline`, `readOnly`. Depends on T144. Proving test: `web/src/editor/useClipEdit.test.ts` (both `useTimelineEditor.test.ts` cases ported: edits made while a save is in flight are kept, dirty, on the new version; the server's copy is adopted when nothing changed; plus: a v1 edit opens not dirty with `savedIsV1`; `v1Foreign` opens with `readOnly` set; `applyServerState` is one undo step and reads as saved; undoing back to the saved snapshot reads as saved; the PUT body carries `schema_version: 2` and `version`, absent for version 0); mutation: adopt the server copy unconditionally, the in-flight case red.

**Checkpoint**: backend route live behind tests; editor state loads, edits, undoes and saves schema v2 with no UI.

---

## Phase 3: User Story 1 - Cut and arrange the clip on a timeline (Priority: P1)

**Goal**: the OpenCut timeline over the store: tracks, selection, split, trim and extend by dragging, moves, zoom, keyboard, properties.

**Independent Test**: mount the timeline with a fixture edit (no route, no preview) and drive split, trim, delete, move, undo/redo and the properties panel; the store's `toTimeline()` matches the 016 rules (`timeline.test.tsx`, `properties-panel.test.tsx`).

- [x] T146 (done: commit `855b269`) [P] [US1] [FR-010] Base UI wrappers in `web/src/components/ui/`: `context-menu.tsx`, `menu.tsx`, `tooltip.tsx`, `scroll-area.tsx`, `slider.tsx`, `textarea.tsx` on `@base-ui/react` 1.8.0 in the repo's shadcn `base-nova` style (`web/components.json`). Proving test: `web/src/components/ui/editor-primitives.test.tsx` (right-click opens the context menu and an item's `onClick` fires and closes it; the menu opens from its trigger; tooltip content shows on focus; the slider changes value from the keyboard; the textarea forwards `maxLength`).
- [x] T147 (done: commit `cc6e4df`) [P] [US1] [FR-009, FR-010, FR-011] Adapt hooks in `web/src/editor/opencut/hooks/`: `use-timeline-playhead.ts` (constant 30 fps instead of the project store), `use-timeline-element-resize.ts` (segment edges through `trimSegment`, bounded by the source and the neighbours' source windows; overlay edges bounded by the reel; no handles for captions or voice-overs; no media store), `use-playback-controls.ts` (research R12: Space, S, Delete/Backspace, Cmd/Ctrl+Z, Shift+Cmd/Ctrl+Z, Cmd/Ctrl+Y; ignored in input, textarea and contenteditable; split only when `canSplitAtPlayhead`, else the reason is toasted). Depends on T144. Proving test: `web/src/editor/opencut/hooks/timeline-hooks.test.ts` (a ruler click snaps to 1/30 s; dragging a segment's right edge stops at the next segment's source start; S splits when allowed and toasts the reason when not; keys typed in a textarea do nothing; the undo/redo chords call the store; Cmd/Ctrl+S no longer splits); mutation: remove the focus guard, red.
- [x] T148 (done: commit `3be9491`) [US1] [FR-008, FR-009, FR-010, FR-015] Adapt `web/src/editor/opencut/components/timeline-element.tsx`, `timeline-track.tsx` and `timeline-playhead.tsx`: Radix to the T146 wrappers, Tailwind 4 renames (research R14), no media store and no drops, audio drawn as a plain bar with a `ponytail:` comment (add waveforms when voice-over editing needs them), no duplicate, freeze frame, separate audio or replace media, menu items only where they apply. Depends on T146, T147. Proving test: `web/src/editor/opencut/components/timeline.test.tsx`, element and track cases (clicking a caption selects it and never removes it; a segment's context menu offers Split, Split keep left, Split keep right, Delete, and Split splits; a caption's menu has no Split; deleting the last segment is refused with a toast; an audio element renders no canvas or waveform; a segment has no horizontal drag; an overlay dropped on a caption track stays on an overlay track).
- [x] T149 (done: commit `00bc47b`) [US1] [FR-008, FR-010, FR-011, FR-012] Adapt `web/src/editor/opencut/components/timeline.tsx`: ruler, zoom, scroll sync, selection box, playhead, toolbar (play/pause, split, keep left, keep right, delete, undo, redo, zoom in/out), track labels Video / Captions / Text / Voice-over; the CommonJS `require` of `timeline-constants` becomes an import; no media drop handlers; split controls take `canSplitAtPlayhead` and show its reason. Depends on T148. Proving test: `web/src/editor/opencut/components/timeline.test.tsx`, container cases (four tracks render from the `multiSegment` fixture plus an overlay and a voice-over; zoom in doubles the content width; a drag-out selection box selects the overlays it crosses; toolbar Split is disabled with the reason when `canSplitAtPlayhead` is false; undo/redo buttons follow the store; a ruler click moves the playhead).
- [x] T150 (done: commit `ea5b8e0`) [P] [US1] [FR-009] Adapt `web/src/editor/opencut/components/properties-panel/index.tsx` and `text-properties.tsx` to schema v2 fields only (research R9): a caption shows its text ("at most 500 characters") and a note when its word count no longer matches its words; an overlay shows text ("at most 500 characters", "at most 10 line breaks"), font size ("integer in [6, 400]"), colour and x/y in [0, 1]; no font picker, rotation, opacity, background, alignment, weight or decoration; an empty state for nothing, a segment or a voice-over. Depends on T144, T146. Proving test: `web/src/editor/opencut/components/properties-panel.test.tsx` (editing an overlay's text and font size reaches `toTimeline()` with only those two fields changed; 500 clamps to 400; an 11th line break is refused; a caption shows only its text; no rotation or opacity control renders); mutation: remove the clamp, red.

**Checkpoint**: the timeline and properties panel work on a fixture edit; nothing is mounted on the page yet, so `main` stays the 016 editor.

---

## Phase 4: User Story 2 - See the edit before rendering (Priority: P2)

**Goal**: the clean-source preview with captions and overlays in the render's font and looks, plus the rendered-clip view.

**Independent Test**: mount the preview with a two-segment edit and a mocked `<video>`; seeking, boundaries, mute, caption look, overlay position and the fallback behave as specified (`preview-panel.test.tsx`, `preview.test.ts`).

- [x] T151 (done: commit `989ec5b`) [P] [US2] [FR-014] Preview assets: copy `app/assets/fonts/Anton-Regular.ttf` and `app/assets/fonts/OFL.txt` to `web/public/fonts/`; `web/src/editor/editor.css` with `@font-face { font-family: "Reelsmith Caption"; src: url("/fonts/Anton-Regular.ttf") format("truetype"); }`; `web/src/editor/caption-looks.json` (keys `default`, `static`, `hormozi`, `mrbeast`, `karaoke`, `boldpop`, `subtle`, each `font_size`, `fill`, `highlight_fill`, `stroke_fill`, `highlights`); `web/src/editor/reel-geometry-cases.json` (source sizes with the expected canvas, inset height and band anchor; landscape cases and the portrait cases 576x1024, 1080x1920 and 1080x2340, whose band is shorter than `canvas_h // 10`, so the anchor is the PR #96 fallback `int(canvas_h * 0.85)`, research R11). Proving test: `tests/unit/test_editor_preview_assets.py` (sha256 of the two font files equal; each look equals `subtitle_image_service.caption_style_for_preset(name)`, `default` being `None`; each geometry case equals `clip_service.reel_geometry`); mutation: one changed font byte, colour or case, each red.
- [x] T152 (done: commit `039ca4f`) [US2] [FR-013, FR-014] `web/src/editor/previewClock.ts` (reel time to source time through the segments, the segment index at a reel time, the next boundary) and `web/src/editor/previewLayout.ts` (`reel_geometry` mirror, font scale = preview width / canvas width; the anchor is the band middle unless the band is shorter than `canvas_h // 10`, then `int(canvas_h * 0.85)`, Python `int()` as `Math.trunc` and `//` as `Math.floor`). Depends on T151 (geometry fixture). Proving test: `web/src/editor/preview.test.ts` (segments [10, 14] and [20, 25]: reel 5 s is source 21 s, the boundary at reel 4 s continues at source 20 s, past the reel is null; every case of `reel-geometry-cases.json`, e.g. 1920x1080 gives canvas 1080x1920, inset 608, band anchor 1592, and 1080x2340 gives inset 2340 and the fallback anchor 1632).
- [x] T153 (done: commit `07a58c1`) [US2] [FR-013, FR-014, FR-015, FR-016] Adapt `web/src/editor/opencut/components/preview-panel.tsx` with the sync logic of OpenCut's `components/ui/video-player.tsx` (plan §7): source view (one `<video src="/api/jobs/{job_id}/source" preload="metadata">` in a 9:16 frame, synced to the playback store, seek at segment boundaries, `muted` from `mute_source`, `loadedmetadata` to `setSourceDuration`, `error` to `onUnavailable(reason)`; caption layer in `"Reelsmith Caption"` with `caption-looks.json` colours and size and the active word highlighted when the look highlights; overlay layer at the x/y fractions; text as React text nodes only) and result view (`/api/clips/{clip_id}/video`, `?v={videoVersion}` once re-rendered). Depends on T140, T144, T151, T152. Proving test: `web/src/editor/opencut/components/preview-panel.test.tsx` (src is `/api/jobs/job-1/source`; seeking the store to reel 5 s sets `currentTime` to 21; a `timeupdate` at source 14 seeks to 20; `mute_source` sets `muted`; the active caption renders in `"Reelsmith Caption"` with the hormozi fill and its active word in the hormozi highlight; `subtle` highlights nothing; an overlay at x 0.1, y 0.3 sits at 10% / 30%; caption text `<b>x</b>` renders literally; a video `error` calls `onUnavailable` with a reason; the result view's src is `/api/clips/clip-1/video?v=2` at `videoVersion` 2; `loadedmetadata` calls `setSourceDuration`).

**Checkpoint**: the preview works on a fixture edit; still not mounted.

---

## Phase 5: User Story 3 - Keep the render, voice-over and save flow working (Priority: P1)

**Goal**: swap the page onto the new editor with every 016 guarantee intact, then delete the old timeline and hook. P1, but last: it mounts US1 and US2.

**Independent Test**: the three existing route test files pass with unchanged expected outcomes, plus the new preview route test.

- [x] T154 (done: commit `6ec76e9`) [US3] [FR-018, FR-019, FR-020, FR-021] Rewrite `web/src/routes/clips.$clipId.edit.tsx` on the new editor: `useClipEdit` instead of `useTimelineEditor`, the preview panel instead of the `<video>`, the timeline instead of `MultiTrackTimeline`, the properties panel when an element is selected (else the active tool panel), `EditorToolPanels` fed through `toTimeline` / `replaceFromTimeline`, the transcript's `CaptionRow` through `updateTextElement`; keep the top bar, Save states, Export, the regenerate-copy checkbox, B-roll credits and back link; import `web/src/editor/editor.css`. Depends on T143, T145, T149, T150, T153. Proving test: `web/src/routes/clips.$clipId.edit.test.tsx` and `web/src/routes/clips.$clipId.edit.tools.test.tsx` green with unchanged expected outcomes (only selectors that named the old timeline change); mutation: skip the save before a re-render, "saves pending edits, then queues the re-render" red.
- [x] T155 (done: commit `94cbb95`) [US3] [FR-013, FR-016, FR-017, FR-018, FR-019] Wire the source/result toggle and the fallback, and `playheadOnEditClock` ([data-model.md §2](data-model.md)) into the timeline's split controls, the keyboard handler, the context menu, `TrimPanel` (`playheadStale`) and the transcript highlight; keep the live SSE effect (`/api/jobs/{job}/events?live=true`, this clip's `ClipRendered` / `ClipRerenderFailed`), the re-render lock, 409 handling, the voice-over flow and its messages as in 016. Depends on T154. Proving test: `web/src/routes/clips.$clipId.edit.review.test.tsx` green in the result view with unchanged expected outcomes (every 016 gating, lock and voice-over case) and new `web/src/routes/clips.$clipId.edit.preview.test.tsx` (source view: trim, then Split at the playhead splits with no re-render; result view with a stale cut: Split disabled with the 016 reason from the toolbar, the S key and the context menu; a source `error` falls back to the result view with a notice and keeps the gate; a 409 "already in progress" shows the reason and leaves Re-render enabled; a voice-over lands on the audio track and Undo removes it); mutation: `playheadOnEditClock` always true, review cases red.
- [x] T156 (done: commit `b638d59`) [US3] [FR-024] Delete `web/src/components/editor/MultiTrackTimeline.tsx`, `web/src/components/editor/MultiTrackTimeline.test.tsx`, `web/src/hooks/useTimelineEditor.ts`, `web/src/hooks/useTimelineEditor.test.ts`; fix any comment naming them (e.g. `web/src/lib/timelineEdit.ts`, `EditorToolPanels.tsx`). Depends on T155. Proving test: `rg -n "MultiTrackTimeline|useTimelineEditor" web/src` prints nothing; `cd web && pnpm test && pnpm build` green (incl. `web/src/routeTree.test.ts`).

**Checkpoint**: the page runs on the new editor; the old timeline and hook are gone.

---

## Phase 6: Browser verification

- [ ] T157 [FR-001, FR-002, FR-013..FR-021] `playwright-cli` flow in dark and light, plus the live route checks, per [quickstart.md](quickstart.md) §3-§4: `scripts/start.sh`; curl the source route (`206 1024`, `416`, `404`); open a completed clip's editor, play, split at the playhead, edit a caption (properties panel and transcript), pick the hormozi style, add and drag a text overlay, add a voice-over, Re-render, wait for "Re-render finished", switch to the rendered clip and play it; screenshots in both colour schemes. Depends on T156. Done when: the curl codes are pasted; both themes' screenshots show no contrast or layout defect; the split shows in the preview at once; the caption is in the chosen style and `document.fonts.check('48px "Reelsmith Caption"')` is true; the voice-over item is on the audio track (Piper, or the stub warning shown); the result view's `currentTime` advances; every source request after the first seek is a 206. The flow covers spec 016's T134 steps: tick T134 with the same evidence. Proving test: the pasted command output and screenshot paths (scratchpad, not committed).

---

## Phase 7: Polish & Cross-Cutting Concerns

- [x] T158 (done: this docs commit) [P] [FR-022] Final `VENDOR.md` OpenCut entry: source (fork `maseko-lucky-9/OpenCut` of OpenCut-app/OpenCut, MIT), commit `3037a7ecbf23ac8db4a3a237eecfd72af9068258`, location `web/src/editor/opencut/`, the copied, adapted and skipped lists ([research.md R2](research.md)), each file's local changes, upgrade notes (React 19 types, Base UI, Tailwind 4, the key map; re-run `vendor.test.ts`). Depends on T157. Proving test: `web/src/editor/vendor.test.ts` extended to check that every file under `web/src/editor/opencut/` is named in the `VENDOR.md` OpenCut entry; mutation: remove one name from the entry, red.
- [x] T159 (done: this docs commit) [all] Docs close-out: spec Status table (real states and proving tests), tasks ticked with commit hashes, ADR-015 status, the 017 rows in `specs/README.md` and `tasks/todo.md`, `CLAUDE.md` tech stack (editor in `web/src/editor/`, zustand), `docs/architecture.md` editor paragraph. Depends on T157. Proving test: relative links in the changed files resolve; `pytest` and `cd web && pnpm test` summaries pasted in the PR.
- [ ] T160 [defer] Voice-overs audible and drawn as waveforms in the edit preview: a `GET /clips/{id}/audio/{src}` route with ADR-014's bare-name rules (`resolve_audio_src`) and a waveform (wavesurfer.js or server-computed peaks) replacing the `ponytail:` bar. Done when: the preview plays the voice-over over the ducked source, and a `src` with a separator, `..` or a symlink out of the clip folder answers 404.
- [ ] T161 [defer] Retime captions on the timeline: move, resize and split captions, re-partitioning `words`. Done when: a caption split at a word boundary gives two captions whose words keep their timings, and a resize clamps the words to the new span as the server does (`timeline_render_service._caption_words`).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: T140 needs T139; T142 needs T141; T144 needs T142; T145 needs T144. T141 and T143 need nothing. Blocks every story.
- **US1 (Phase 3)**: after Phase 2. T148 needs T146 and T147; T149 needs T148; T150 needs T144 and T146.
- **US2 (Phase 4)**: after Phase 2; independent of US1. T152 needs T151; T153 needs T140, T144, T151, T152.
- **US3 (Phase 5)**: needs US1 (T149, T150), US2 (T153), T143 and T145. T155 needs T154; T156 needs T155.
- **Verification (Phase 6)**: T157 needs T156.
- **Polish (Phase 7)**: T158 and T159 need T157. T160 and T161 are deferred (not part of this spec's done).

### User Story Dependencies

- **US1 (P1)**: Foundational only.
- **US2 (P2)**: Foundational only (shares the store with US1, not its components).
- **US3 (P1)**: US1 and US2 (it mounts both on the page).

```text
T139 ─ T140 ─────────────────────────────┐
T141 ─ T142 ─ T144 ─┬─ T145 ─────────────┤
T143 ───────────────┼────────────────────┤
                    ├─ T147 ─┐           │
T146 ───────────────┼────────┴─ T148 ─ T149 ─┐
                    └─ T150 (also T146) ─────┤
T151 ─ T152 ─ T153 (also T140, T144) ────────┤
                                             └─ T154 ─ T155 ─ T156 ─ T157 ─┬─ T158
                                                                           └─ T159
```

### Within Each Task

- Write the proving test first and see it fail; implement; see it pass; mutation-check; run `ruff check app tests scripts`, `pytest`, `cd web && pnpm test && pnpm build`.

### Parallel Opportunities

- Phase 1-2: T139, T141 and T143 together (backend, vendored base, rendered-cut module touch different files).
- After T144: T146, T147 (US1) and T151 (US2) together; T150 beside T148/T149 once T146 is done.
- US1 and US2 can proceed in parallel after Phase 2.
- T158 and T159 together after T157.

---

## Parallel Example: Foundational

```bash
Task: "T139 range_response() in app/routers/media.py + tests/unit/test_http_range.py"
Task: "T141 zustand + web/src/editor/opencut/ copies + web/src/editor/vendor.test.ts"
Task: "T143 web/src/editor/renderedCut.ts + web/src/editor/renderedCut.test.ts"
```

## Parallel Example: User Story 1 and User Story 2

```bash
Task: "T146 Base UI wrappers in web/src/components/ui/ + editor-primitives.test.tsx"
Task: "T147 timeline hooks in web/src/editor/opencut/hooks/ + timeline-hooks.test.ts"
Task: "T151 web/public/fonts/, caption-looks.json, reel-geometry-cases.json + tests/unit/test_editor_preview_assets.py"
```

---

## Implementation Strategy

### Shippable increments (each leaves `main` green and the 016 editor working)

1. **Backend** (T139-T140): the source route and the 416 fix; usable on its own (curl).
2. **Core** (T141-T145): vendored base, adapter, store, persistence; no UI change.
3. **Timeline** (T146-T150): components behind tests, not mounted.
4. **Preview** (T151-T153): preview behind tests, not mounted.
5. **Swap** (T154-T156): the page moves to the new editor and the old code goes. This is the user-visible step; US1-US3 become real together.
6. **Verify and document** (T157-T159).

House practice ships one PR per spec through `/ship`; the increments are commit groups inside it, each green on its own so a reviewer (or a revert) can stop at any boundary.

### MVP

Increments 1-5: the swap is the first point a user sees value, and it must carry all of US3 (no regression), so the MVP is the whole of Phases 1-5.

---

## Notes

- Total: 23 tasks (T139-T161): 21 to do in this spec, 2 deferred (T160, T161).
- Per phase: Setup 1, Foundational 6, US1 5, US2 3, US3 3, Verification 1, Polish 4 (2 of them deferred).
- `[P]` tasks: T139, T141, T143, T146, T147, T150, T151, T158.
- Never weaken a 016 test to make the swap pass: an expected outcome that changes is a regression unless the spec says otherwise (only selectors naming the deleted timeline may change).
- Commit after each task; tick it with the commit hash, as specs 013-016 do.
