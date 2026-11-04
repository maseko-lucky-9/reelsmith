# Feature Specification: OpenCut timeline as the clip editor

**Feature Branch**: `feat/017-opencut-timeline-editor` (spec directory `specs/017-opencut-timeline-editor`)

**Created**: 2026-11-04

**Status**: Implemented; browser check (T157) open (decisions final: owner plan "Phase 4b", no `/speckit-clarify`)

**Plan**: [plan.md](plan.md) | **Tasks**: [tasks.md](tasks.md) | **Decision**: [ADR-015](../../docs/decisions/015-opencut-timeline-port.md) | **Builds on**: [spec 016](../016-editor-render/spec.md), [ADR-014](../../docs/decisions/014-timeline-schema-v2.md) (timeline schema v2)

**Input**: User description: "Port OpenCut's browser timeline (MIT) into the web app as the editing UI over timeline schema v2: a real multi-track timeline (select, split, trim by dragging, zoom, undo/redo) and a preview of the clean source with the captions and text drawn on top, while keeping every spec 016 guarantee (split gated on the rendered cut, re-render lock and live result, 409 on a duplicate re-render, voice-over flow, Save/Re-render/Export, undo/redo). The server owns media and rendering; the browser never processes media."

## Problem

Spec 016 made the editor's edits render, but its timeline is a row of static bars: clicking a caption, text or voice-over bar deletes it, and the video segments are locked on the timeline and edited by typing source seconds into a Trim panel. There is no selection, dragging, resizing, zoom or keyboard control. The preview plays the rendered clip, whose captions are burned in, so a caption, style or text change is invisible until a re-render, and after a trim the playhead runs on the old cut's clock, so Split waits for a re-render. The owner's decision (plan "Phase 4b") is to take OpenCut's MIT timeline as the editing surface instead of growing the 016 one.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cut and arrange the clip on a timeline (Priority: P1)

An editor opens a clip and sees its edit as stacked tracks: the video segments, the captions, the text overlays and the voice-overs. They select items, split the segment under the playhead, drag a segment's edge to trim or extend it within the source, delete a segment, drag a text overlay or voice-over to another time, resize a text overlay, zoom in and out, and undo or redo any of it. Selecting a caption or a text overlay opens its properties so the text (and an overlay's position, size and colour) can be changed. Captions and other items stay on the speech they belong to when the cut changes.

**Why this priority**: it is the feature: the editing surface replaces the static bars and the typed-seconds trimming.

**Independent Test**: load a saved schema v2 edit into the timeline, perform split, trim, delete, move and undo/redo, and check that the edit produced is the one the 016 rules give for the same operations (captions follow the source, a voice-over keeps its length), with no UI beyond the timeline itself.

**Acceptance Scenarios**:

1. **Given** a clip whose edit has one segment and captions, **When** the editor places the playhead mid-segment and splits, **Then** the video track shows two segments meeting at the playhead and every caption keeps its place on the reel.
2. **Given** two segments, **When** the editor drags the first segment's left edge 2 s later, **Then** the reel is 2 s shorter, the captions after it move 2 s earlier and captions whose speech was cut away disappear.
3. **Given** a segment whose source continues past its end, **When** the editor drags its right edge later, **Then** it extends up to the source's end or the next segment's start in the source, whichever comes first, and no further.
4. **Given** a text overlay is selected, **When** the editor changes its text and drags it 1 s later, **Then** the overlay shows the new text at the new time and Undo twice restores the original.
5. **Given** a caption bar, **When** the editor clicks it, **Then** it is selected (never deleted); Delete removes the selected items.
6. **Given** only one segment remains, **When** the editor tries to delete it, **Then** it is refused with a reason.

---

### User Story 2 - See the edit before rendering (Priority: P2)

The editor presses play and sees the clean source video play through the edited segments back to back, with the captions drawn on top in the typeface and colours the render will use (the chosen caption style, the active word highlighted where the style highlights) and the text overlays at their positions. A trim or split shows up in the preview at once, and the playhead is always on the edited reel's clock.

**Why this priority**: it removes the "re-render to see it" loop and makes Split usable right after a trim. It depends on the timeline (US1) for the edit it previews.

**Independent Test**: point the preview at a job's source with a two-segment edit, seek to a reel time, and check the frame shown is from the expected source time and the caption on screen is the caption at that reel time, in the render's font.

**Acceptance Scenarios**:

1. **Given** an edit with segments [10 s, 14 s] and [20 s, 25 s], **When** the editor seeks the playhead to 5 s, **Then** the preview shows source time 21 s.
2. **Given** playback in the first segment, **When** it reaches the segment's end, **Then** playback continues from the next segment's start without showing the source between them.
3. **Given** the caption style "hormozi", **When** a caption is on screen, **Then** its words are in that style's colours, in the same typeface as the rendered clip, with the active word highlighted.
4. **Given** "mute source" is on, **When** the preview plays, **Then** the source audio is silent.
5. **Given** a job whose source video is no longer kept, **When** the editor opens, **Then** the preview falls back to the rendered clip and says why.

---

### User Story 3 - Keep the render, voice-over and save flow working (Priority: P1)

Everything the 016 editor guaranteed still holds on the new editor: Save sends the edit and keeps edits made while it was in flight; Re-render saves first, locks the controls until this clip's result arrives on the job's live event stream, refreshes the rendered clip and adopts the cut it was rendered from; a duplicate re-render is refused with the server's reason; the voice-over panel speaks the AI hook by default, saves first, adopts the server's edit and warns when the voice was a silent placeholder; Export downloads the rendered clip; the rendered clip can be played in the editor; and Split is never offered on a playhead that runs on a clock other than the edit's.

**Why this priority**: the new editor replaces the old one; any regression here loses work or renders the wrong cut. It lands last because it swaps the page once US1 and US2 are green.

**Independent Test**: run the 016 editor behaviour tests (gating, re-render lock and live events, voice-over messages, save in flight) against the new editor page with their assertions unchanged.

**Acceptance Scenarios**:

1. **Given** unsaved edits, **When** the editor presses Re-render, **Then** the edit is saved first, the re-render is queued, and Save, Re-render, voice-over generation and caption regeneration stay disabled, with the reason shown, until this clip's "rendered" or "re-render failed" event arrives.
2. **Given** a re-render is already running on the server, **When** another is requested, **Then** the server's "already in progress" reason is shown and nothing is left locked.
3. **Given** the rendered-clip view and an edit whose segments differ from the cut the clip file was rendered from, **When** the editor looks for Split at the playhead, **Then** it is disabled with the 016 reason, and stays disabled while the rendered cut is unknown.
4. **Given** the clean-source view, **When** the editor trims and then splits at the playhead, **Then** the split happens at once (the playhead is on the edit's clock).
5. **Given** a voice-over is generated with no Piper voice installed, **When** it returns, **Then** the voice-over item appears on the audio track, a warning says it is a silent placeholder, and Undo removes it.
6. **Given** a save is in flight, **When** the editor makes another change, **Then** that change is kept and still shows as unsaved after the save answers.

### Edge Cases

- A saved schema v1 edit opens converted to v2 (through the clip's window) without counting as an unsaved change; the voice-over flow still saves it as v2 first (the server refuses voice-overs on a saved v1 edit).
- A v1 edit that cannot be converted (no known window, or a video item reading another file) opens read-only on the timeline with the reason; Re-render of the saved edit and Export still work.
- An edit with more than one video track (Reelsmith never writes one; the server plays them as one list) opens merged onto one video track, which counts as an unsaved change.
- The source duration is not known until the source loads: until then the editor bounds extension by the job's recorded duration, else by the furthest segment end; the server re-checks every save (422 with the reason).
- A trim or split that would leave a segment under 0.05 s, overlap a neighbour's source window or run past the source is refused with a reason, and nothing changes.
- A text overlay or voice-over dragged past the reel's end is stopped at the end; items left past the end after a trim are trimmed or dropped as in 016.
- The rendered cut reported as "unknown" (a render is replacing the file) never enables Split in the rendered-clip view; no recorded cut means the clip's own window.
- Keyboard shortcuts are ignored while typing in a text field.
- The live event stream closes when the editor closes; events for other clips of the same job are ignored.
- A range request outside the source file is refused (unsatisfiable range) instead of answering a broken partial response.
- Caption and overlay text containing markup shows literally; it is never interpreted.

## Requirements *(mandatory)*

Status tags follow the constitution (`Implemented | Partial | Scaffolded-unwired | Missing`); every requirement started **Missing**; the Status table at the end has the current states.

### Clean source for preview (server)

- **FR-001** *(Missing)*: The server MUST offer a job's source video for playback in the editor, read only from the source path the job recorded (never a path the client supplies), answering a request without a byte range with the whole file and advertising byte ranges, and a single byte range with a partial response (start, end and total size), so the preview can seek without downloading the whole source.
- **FR-002** *(Missing)*: The source request MUST be refused with "not found" for an unknown job, a job with no recorded source (never kept, or removed by retention), a recorded path that is unsafe (NUL byte, relative, `..` component), resolves outside the folders the app manages, or is not a regular file; with "conflict" while the job is being deleted; and with "range not satisfiable" (stating the file size) for a range that starts beyond the file or ends before it starts. A refusal never reveals the server path. It MUST sit behind the same access control as the other media routes.
- **FR-003** *(Missing)*: The rendered-clip media route MUST share that range handling, so it too refuses an unsatisfiable range instead of sending a partial response with a negative length; its other behaviour is unchanged.

### Timeline model

- **FR-004** *(Missing)*: The editor's timeline MUST represent a schema v2 edit (ADR-014) one to one: the segments on one video track, each caption, text-overlay and audio track as its own timeline track in the original order, and the caption style and mute flag. Opening an edit and saving it unchanged MUST send back exactly the edit that was loaded (every field, including the ones the editor does not edit and fields it does not know); times and positions the editor changes are rounded to the millisecond (positions to four decimals), those it does not change are kept as loaded.
- **FR-005** *(Missing)*: A saved v1 edit MUST open converted to v2 by the 016 rules (through the clip's window) and MUST NOT count as an unsaved change by itself; it is saved as v2. A v1 edit that cannot be converted MUST open read-only with the reason.
- **FR-006** *(Missing)*: Segments MUST stay in source order and back to back on the reel (no gaps, no reordering), each at least 0.05 s long and inside the source range known to the editor; a segment may be extended beyond the clip's original window up to the source's bounds and its neighbours' source windows. An edit breaking a rule is refused with a reason and changes nothing.
- **FR-007** *(Missing)*: When the segments change (split, trim, extend, delete), captions, text overlays and voice-overs MUST follow the source under them exactly as in the 016 editor: an item whose source is cut away is trimmed or dropped, a caption keeps the word timings of its surviving words, a voice-over keeps its length, is cut at the reel's end and is dropped when its start is cut away.
- **FR-008** *(Missing)*: Users MUST be able to split the segment under the playhead (at least 0.05 s from either edge), and split keeping only the left or only the right part (trim at the playhead). Captions, text overlays and voice-overs are not split in this phase.
- **FR-009** *(Missing)*: Users MUST be able to drag a segment's edges (trim or extend), move a text overlay or a voice-over in time, resize a text overlay, and delete any item except the last segment. Captions are selected and edited but not moved or resized (their timing is the speech). A voice-over is moved but not resized (its length is its audio). Items move only within tracks of their own kind; segments are never dragged along the reel.
- **FR-010** *(Missing)*: Users MUST be able to select an item by click, add to the selection with a modifier click or a drag-out selection box, open a context menu on an item (split, split keep left, split keep right, delete, as applicable), and use the keyboard: play/pause, split at the playhead, delete the selection, undo and redo. Keyboard shortcuts MUST NOT fire while typing in a text field. Features that do nothing are not offered (precedent T013).
- **FR-011** *(Missing)*: Users MUST be able to zoom the timeline in and out, scrub the playhead by clicking or dragging the time ruler, and see the reel's length; the playhead snaps to frames.
- **FR-012** *(Missing)*: Every change (including a voice-over the server appends) MUST be one undoable step restoring the whole edit, with at least 50 steps of history; a new change clears redo. Whether the edit has unsaved changes follows the saved copy (undoing back to it reads as saved).

### Preview

- **FR-013** *(Missing)*: The edit preview MUST play the job's clean source through the segments back to back, with the playhead on the edited reel's clock: seeking to a reel time shows the matching source time, and reaching a segment's end continues at the next segment's start. "Mute source" silences the source in the preview. When the source cannot be played (not kept, refused, fails to load), the editor MUST fall back to the rendered clip and say why.
- **FR-014** *(Missing)*: The preview MUST draw the caption active at the playhead and every text overlay active at the playhead over the video, laid out on the reel's 9:16 frame the way the render lays out a letterboxed reel (the source fitted to the frame's width and centred, captions in the band below it), in the typeface the server renders with by default (the same font file), in the chosen caption style's colours and size, with the active word highlighted where the style highlights. Overlay positions are the edit's fractions of the frame. Caption and overlay text is shown as text, never interpreted as markup.
- **FR-015** *(Missing)*: Voice-overs MUST appear on the audio track as plain bars (no waveform). They are not heard in the edit preview; they are heard in the rendered clip.
- **FR-016** *(Missing)*: Users MUST be able to switch the preview to the rendered clip (the result of the last render) and play it; after this clip is re-rendered the rendered-clip view shows the new file, not a cached one.

### Kept from spec 016

- **FR-017** *(Missing)*: Whenever the playhead is the rendered clip's clock (the rendered-clip view, or the fallback when the source cannot be played), split at the playhead and split keeping left or right MUST be offered only when the edit's segments are the cut that file was rendered from: the server's recorded cut, replaced by the cut a "rendered" event for this clip carries; no record means the clip's own window; an empty record means "unknown" and never matches; boundaries match within 1 ms each and a different number of segments never matches. While they do not match, the caption under that playhead is not highlighted and the disabled split says why. In the clean-source view the playhead is the edit's own clock, so split is offered whenever the playhead is inside a segment.
- **FR-018** *(Missing)*: Re-render MUST save pending changes first (a failed save queues nothing). From the queue acknowledgement until this clip's "rendered" or "re-render failed" event on the job's live event stream (no history replay), Save, Re-render, voice-over generation and caption regeneration MUST be disabled with the reason shown. A "rendered" event refreshes the rendered clip and adopts the cut it carries; a "re-render failed" event unlocks and shows the error. A refused re-render (409, e.g. "already in progress") shows the server's reason and leaves nothing locked. The "regenerate title, summary and hashtags" option is sent with the request. The live stream is opened for the clip's job and closed when the editor closes; events for other clips are ignored.
- **FR-019** *(Missing)*: The voice-over panel MUST default its script to the clip's AI hook, save first when there are unsaved changes or the saved edit is v1, adopt the edit the server returns as one undoable step, warn when the server used the silent placeholder voice, show the 016 messages for 409 (including the "edit changed" lost-update case), 503 ("busy" and "unavailable"), 502 and other failures, and be locked during a re-render; Save and Re-render wait while it runs.
- **FR-020** *(Missing)*: Save MUST send the edit with the version it was based on, show "Saving…", "Save changes" or "Saved", show the server's reason on a refusal, and keep changes made while it was in flight (still unsaved, on the new version).
- **FR-021** *(Missing)*: The editor MUST keep: Export (download of the rendered clip), the transcript panel's caption text editing and seeking, the caption style picker, the text overlay and trim tool panels, the B-roll credits, the back link to the job, and undo/redo buttons.

### Vendored code

- **FR-022** *(Missing)*: Code taken from OpenCut MUST keep OpenCut's MIT notice (the licence text beside it and an origin line in every taken file naming the upstream file and revision) and MUST be recorded in the repository's vendored-code register with its source, revision, what was copied, adapted and skipped, and the local changes.
- **FR-023** *(Missing)*: The editor MUST NOT process or store media in the browser: no in-browser transcoding or export, no browser-side project or media storage, no media import panel. The server owns media and rendering.
- **FR-024** *(Missing)*: The 016 timeline component and editor state hook MUST be removed once the new editor passes its tests, and nothing may still use them.

### Key Entities

- **Edit (schema v2)**: what the server stores and renders (ADR-014): source segments in source seconds; captions (text with per-word timings), text overlays (text, position, size, colour) and voice-overs (a server-named audio file and a gain) on the edited reel's clock; a caption style; a mute flag; a version for lost-update checks.
- **Timeline**: the editor's working form of one edit: tracks of elements with a start, a length and trims; one video track holding the segments as windows of the whole source; text tracks for captions and overlays; an audio track for voice-overs; a selection; an undo/redo history of whole snapshots.
- **Source video**: the job's downloaded video the segments are cut from, offered read-only for preview; it disappears when retention removes it.
- **Rendered cut**: the list of source windows the clip's current file was rendered from, reported by the server (record and "rendered" events); decides whether the rendered clip's clock is the edit's clock.
- **Voice-over item**: an audio item on the edit pointing at a server-written file in the clip's folder; created only by the server.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In one editor session, without leaving the page, an editor can split a clip, edit a caption, add a voice-over, re-render and play the result; excluding render time, the edits take under 3 minutes.
- **SC-002**: A trim or split is visible in the preview within 1 second, with no re-render.
- **SC-003**: Every schema v2 edit in the round-trip corpus (the server's default edit and edits with several segments, worded captions, styled overlays, voice-overs, a caption style, a mute flag and unknown fields) comes back byte-for-byte equal after open and save with no change: 100%.
- **SC-004**: The preview's caption typeface is the same font file the server renders with by default (checked by content hash), and its caption colours and sizes are the render's for all six styles and the default look.
- **SC-005**: Every spec 016 editor behaviour check (split gating, re-render lock and live events, voice-over messages, save in flight) passes on the new editor with its expected outcomes unchanged.
- **SC-006**: Screenshots of the full editor flow in dark and light colour schemes show no contrast or layout defect.
- **SC-007**: Seeking in the preview never downloads the whole source: after the first seek, every source request in the flow is answered as a partial response.

## Assumptions

- One local user on macOS with a Chromium-based browser or Safari; HEVC sources play there. Phones can open the editor but timeline editing is designed for a pointer and keyboard.
- The source video is kept while the job has a live clip (retention rule, spec 001 FR-013); when it is gone, the editor falls back to the rendered clip.
- The preview approximates the render: it lays the source out as a letterboxed reel and does not show the face-tracked crop, B-roll, the blurred background, ducking or the exact line wrapping. The render is the truth.
- The preview font is the bundled default caption font; if the server is configured with a different font file, the preview font differs (the render is the truth).
- Times are seconds; the editor works at millisecond precision, which is what the server stores.
- The OpenCut code taken is the owner's local checkout (July 2025 snapshot, MIT); nothing of OpenCut's media import, storage, export or authentication is used.
- Decisions resolved by default instead of asking (no clarification round, owner instruction): split gating applies to the rendered clip's clock, not to the clean-source view; captions are not moved, resized or split on the timeline; voice-overs are silent in the edit preview; segments cannot be reordered; the rendered-clip route gains the unsatisfiable-range answer.

## Threats

- **T1 (medium, handled in the design).** The source route reads a file named by the database. The path comes only from the job record, is checked for unsafe spellings without touching the filesystem, must resolve below a managed folder and be a regular file (a symlink leaving the folder resolves outside and is refused), and is never echoed (FR-001, FR-002).
- **T2 (low).** Range parsing: a single `bytes=start-end` range is accepted, the end is clamped to the file, a start beyond the file or after the end is refused with 416, and the body streams in bounded chunks (FR-002, FR-003).
- **T3 (low).** Third-party code: MIT, pinned to one revision, no network calls, no storage APIs; the only new runtime dependency is a small state library (FR-022, FR-023).
- **T4 (low).** Caption and overlay text in the page is rendered as text, never as HTML (FR-014).

## Known limitations

- Voice-overs are silent in the edit preview and drawn without a waveform (a `ponytail:` comment names the upgrade; deferred task in tasks.md).
- The preview does not show the face-tracked crop, B-roll, the blurred background, ducking or the render's exact wrapping and stroke.
- Crossing a segment boundary in the preview seeks the source, which can stall for a moment.
- Captions cannot be retimed, resized or split on the timeline.
- Segments cannot be reordered (schema v2 requires source order).

## Out of scope

- OpenCut's media panel and import, browser-side storage, in-browser export or transcoding, waveforms, transitions, keyframes, speed changes, multiple video tracks, freeze frames, duplicating items, separating audio.
- An audio route for hearing voice-overs in the edit preview.

## Status

| Requirement | State | Proving test |
|---|---|---|
| FR-001, FR-002 | Done | `tests/contract/test_job_source_route.py` |
| FR-003 | Done | `tests/unit/test_http_range.py`, `tests/contract/test_media_router.py` |
| FR-004, FR-005 | Done | `web/src/editor/adapter.test.ts` |
| FR-006..FR-009, FR-012 | Done | `web/src/editor/opencut/stores/timeline-store.test.ts`, `web/src/editor/useClipEdit.test.ts`, `web/src/editor/opencut/components/properties-panel.test.tsx` |
| FR-010, FR-011 | Implemented, browser check open (T157) | `web/src/editor/opencut/hooks/timeline-hooks.test.ts`, `web/src/editor/opencut/components/timeline.test.tsx` |
| FR-013..FR-016 | Implemented, browser check open (T157): clock, layout and sync logic tested in jsdom, real playback and caption position not yet seen | `web/src/editor/preview.test.ts`, `web/src/editor/opencut/components/preview-panel.test.tsx`, `tests/unit/test_editor_preview_assets.py` |
| FR-017..FR-021 | Done | `web/src/editor/renderedCut.test.ts`, `web/src/routes/clips.$clipId.edit.review.test.tsx`, `web/src/routes/clips.$clipId.edit.tools.test.tsx`, `web/src/routes/clips.$clipId.edit.preview.test.tsx` |
| FR-022..FR-024 | Done (the old timeline files are deleted, T156; `vendor.test.ts` also checks that `VENDOR.md` names every vendored file) | `web/src/editor/vendor.test.ts`, `pnpm build` with the old files deleted |
| All UI | Open (T157) | `playwright-cli` flow in dark and light (T157) |
