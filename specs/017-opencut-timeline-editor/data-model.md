# Data Model: OpenCut timeline as the clip editor

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Contracts**: [timeline-adapter](contracts/timeline-adapter.md), [source-video](contracts/source-video.md)

No database change: `clip_edits.timeline` stays the JSON column of ADR-014, `clips.rendered_segments` (016) is read as before. Everything below is client state in `web/src/editor/` plus one read-only HTTP resource.

## 1. Stored edit (unchanged, ADR-014)

`TimelinePayload` (`web/src/api/client.ts:462`): `tracks[]` of `{kind, items[]}`, `schema_version: 2`, `caption_style` (a `PRESETS` key or null), `mute_source` (bool). Item shapes and limits are ADR-014's; the ones the editor enforces client-side are quoted here so the tasks can cite them verbatim:

| Rule | Value (server constant) |
|---|---|
| Minimum segment length | 0.05 s (`MIN_SEGMENT_SECONDS`), checked as `end - start` on raw floats; the editor keeps 51 ms (`MIN_EDIT_SEGMENT_SECONDS`), because times are written to the millisecond and a 50 ms span can fall under 0.05 s in float math (50.3 − 50.25 = 0.04999999999999716) |
| Segments | in source order, no overlap, inside the source range; at most 100 (`MAX_ITEMS["video"]`) |
| Reel length | at most 20 minutes (`MAX_OUTPUT_SECONDS`) |
| Reel-clock items | must not end past the reel's end (+1 ms, `TIME_TOLERANCE`) |
| Text per caption or overlay | at most 500 characters (`MAX_TEXT_CHARS`) |
| Words per caption | at most 100 (`MAX_CAPTION_WORDS`, its text split on whitespace); at most 5000 across all captions (`MAX_TIMELINE_CAPTION_WORDS`) |
| Overlay text in all | at most 5000 characters across all text overlays (`MAX_TIMELINE_OVERLAY_CHARS`) |
| Overlay line breaks | at most 10 (`MAX_OVERLAY_LINES`) |
| Overlay `font_size` | integer in [6, 400] (`FONT_SIZE_RANGE`), default 36 |
| Overlay `x`, `y` | fractions of the canvas, centre of the text; defaults 0.5 and 0.1 |
| Overlay `color` | at most 32 characters (`MAX_COLOR_CHARS`), default `#ffffff` |
| Audio `gain` | [0, 4] (`MAX_AUDIO_GAIN`); `src` is a server-chosen bare file name |
| Tracks | at most 16 (`MAX_TRACKS`); several tracks of one kind are concatenated by the server |

## 2. Editor state (OpenCut shapes, Reelsmith extensions)

OpenCut's types (`web/src/editor/opencut/types/timeline.ts`, copied) are kept. Reelsmith adds fields in `web/src/editor/types.ts` by intersection, never by editing the copied file:

```text
TrackRole      = 'segments' | 'captions' | 'overlays' | 'voiceovers'
EditorTrack    = TimelineTrack & { role: TrackRole; originTrack: Omit<v2 track, 'items'> }
SegmentElement = MediaElement & { origin: v2 video item }        # on the one 'segments' track (isMain)
CaptionElement = TextElement  & { origin: v2 caption item }      # role 'captions'
OverlayElement = TextElement  & { origin: v2 text-overlay item } # role 'overlays'
VoiceoverElement = MediaElement & { origin: v2 audio item }      # role 'voiceovers', mediaId = origin.src
```

### Mapping (adapter, `web/src/editor/adapter.ts`)

| Schema v2 | Editor | Notes |
|---|---|---|
| all `video` tracks | one `media` track, `isMain: true`, role `segments` | several video tracks are merged in order (the server concatenates them) and the load counts as a change |
| `video` item `{start, end, src?}` (source s) | `MediaElement` `duration` = source duration D, `trimStart` = start, `trimEnd` = D − end, `startTime` = sum of earlier segment lengths, `mediaId` = `src ?? 'main'` | reel placement is derived, never stored |
| `caption` track | `text` track, role `captions` | one editor track per v2 track, order kept |
| `caption` item `{start, end, text, words[], style?}` | `TextElement` `startTime` = start, `duration` = end − start, `content` = text, other TextElement fields at OpenCut defaults | `words`, `style` and unknown keys stay in `origin` |
| `text-overlay` track | `text` track, role `overlays` | |
| `text-overlay` item `{start, end, text, x, y, font_size?, color?}` | `TextElement` `content` = text, `fontSize` = font_size ?? 36, `color` = color ?? `#ffffff`, `x` = (x − 0.5) × canvas width, `y` = (y − 0.5) × canvas height (OpenCut pixels from the centre, canvas 1080x1920) | |
| `audio` track | `audio` track, role `voiceovers` | |
| `audio` item `{start, end, src, gain}` | `MediaElement` `mediaId` = src, `startTime` = start, `duration` = end − start | `gain` stays in `origin` (no UI) |
| `caption_style` | store `captionStyle: string \| null` | absent key and null are kept apart (`origin` of the timeline) |
| `mute_source` | the `segments` track's `muted` | absent key kept absent until toggled |

### Write-back rule (exact round trip)

`toTimeline(state)` builds each item as `{...element.origin, ...changed}` where `changed` holds only the v2 fields whose editor value differs from the value `fromTimeline` derived from `origin`. Changed times are rounded with `round3` (milliseconds), changed `x`/`y` to four decimals, a changed `font_size` to an integer. Tracks are emitted in their loaded order with `originTrack`'s keys. Consequence: `toTimeline(fromTimeline(t))` deep-equals `t` for every v2 timeline with at most one video track (contract test).

### Store (`web/src/editor/stores/timeline-store.ts`, adapted from OpenCut)

| Field / action | From OpenCut | Change |
|---|---|---|
| `_tracks`, `tracks` (sorted), `getSortedTracks` | kept | `ensureMainTrack` creates the `segments` track only for an empty load |
| `history`, `redoStack`, `pushHistory`, `undo`, `redo` | kept (snapshots) | a snapshot is `EditorSnapshot {tracks, captionStyle, timelineOrigin}`; history capped at 50 (016 `HISTORY_LIMIT`); OpenCut's was unbounded |
| `selectedElements`, `selectElement`, `deselectElement`, `clearSelectedElements`, `setSelectedElements` | kept | |
| `dragState`, `startDrag`, `updateDragTime`, `endDrag` | kept | refused for `segments` elements (ripple) and across roles |
| `addTrack`, `insertTrackAt`, `removeTrack`, `addElementToTrack`, `moveElementToTrack` | kept for `overlays` and `voiceovers` | no media drops; `moveElementToTrack` only between tracks of one role |
| `updateElementTrim`, `updateElementDuration`, `updateElementStartTime` | kept for overlays (start, duration) and voice-overs (start) | segments go through `trimSegment` |
| `splitElement`, `splitAndKeepLeft`, `splitAndKeepRight` | kept as names | `segments` only; implemented through `timelineEdit.splitAt` / `setSegments` (R4) |
| new `trimSegment(id, {start?, end?})`, `deleteSegment(id)` | | through `timelineEdit.setSegments`; refuse below 51 ms as written, overlap, outside the source, a reel over 20 minutes, deleting the last segment; split also refuses a 101st segment. Every segment edit is checked against the server's segment rules (`timelineEdit.segmentsRefusal`) before it is committed |
| `toggleTrackMute` | kept | `segments` track only (= `mute_source`) |
| `updateTextElement` | kept | captions: `content` only; overlays: `content`, `fontSize`, `color`, `x`, `y` |
| `separateAudio`, `replaceElementMedia` | removed | no media import |
| `loadProjectTimeline`, `saveProjectTimeline` (storageService) | removed | replaced by `load(timeline, ctx)` (clears history) and `toTimeline()`; persistence is `useClipEdit` |
| new `replaceFromTimeline(t, from)` | | one history step; how the 016 tool panels (`EditorToolPanels.tsx`) and `applyServerState` write a whole v2 timeline back. `from: 'edit'` (a panel): the segments it adds or changes must keep 51 ms and not be negative, and the list the server's segment rules, else it is refused with the reason (segments it keeps, such as a saved 0.05 s one, are not held to 51 ms); `from: 'server'` (a voice-over answer): taken as it is. The Trim panel checks typed times as written (to the ms) before that, an emptied field is not 0 s, and an end may not pass the source's known length (§3) written to the ms: the server clamps an end within 1 ms past the source back to it before the 0.05 s check and measures the reel from the clamped segments |
| new `sourceDuration`, `setSourceDuration(d, from)` | | re-derives segment `duration`/`trimEnd`, no history entry; `from` is `probe` (the preview's `<video>` metadata) or `job` (`JobState.duration`): a probed length wins whichever arrives first and is kept across `load` (§3) |
| new `readOnly: string \| null` | | reason when a v1 edit cannot be converted |

### Persistence hook (`web/src/editor/useClipEdit.ts`, replaces `useTimelineEditor`)

| Field | Meaning (016 equivalent) |
|---|---|
| `version` | the saved edit's version; 0 for the server's default timeline |
| `savedSnapshot` | `JSON.stringify` of the saved edit in its v2 form (a v1 edit is compared after conversion, so opening it is not dirty) |
| `savedIsV1` | the saved row is v1: the voice-over flow saves first |
| `isDirty` | `JSON.stringify(store.toTimeline()) !== savedSnapshot` |
| `isSaving`, `saveError` | `useMutation` state |
| `save()` | `PUT /clips/{id}/edit {timeline: prepareForSave(sent, window), version: version \|\| undefined}`; on success adopt: version and snapshot always move on; the store adopts the server's copy only if it still equals what was sent (edits made in flight are kept, still dirty) |
| `applyServerState(state)` | a voice-over's returned edit: one undoable step, saved |
| answers after the editor moved on | a save's answer is adopted only by the editor (hook instance) that sent it: once another editor holds the singleton stores (another clip, none, or the same clip opened again, which keeps the version it loaded, so its next save meets a 409 if the old one landed) it only updates that clip's cached edit. A voice-over's answer is applied by an editor of that clip, else only cached. A success toast is shown only while that clip is shown (`isShowing()`); a failure after the page moved on is toasted naming the clip, tagged with its id. The route remounts per clip (`remountDeps`), so no route state carries over |
| `words`, `loadedTimeline` | as in 016 |

### Rendered cut (`web/src/editor/renderedCut.ts`, moved verbatim from the route)

```text
Cut          = readonly {start, end}[]                       # source seconds
renderedCut(rendered, window): Cut | 'unknown' | null
  rendered is an array:  [] -> 'unknown'; else the pairs as a Cut
  rendered null/absent:  window ? [window] : null
sameCut(a, b): same length and every boundary within CUT_TOLERANCE = 1e-3 + 1e-9
cutOf(timeline, window) = segmentsOf(upgradeTimeline(timeline, window) ?? timeline)
```

Source of `rendered`: the `ClipRendered` event's `segments` for this clip once one arrived, else `clip.rendered_segments`.

### Preview state

| State | Values | Rule |
|---|---|---|
| `view` | `'source' \| 'result'` | starts `'source'`; forced to `'result'` (with the reason) when the source request fails or the job has no source |
| `playheadOnEditClock` | bool | `view === 'source' \|\| baseline === null \|\| (baseline !== 'unknown' && sameCut(cutOf(edit), baseline))` with `baseline` = rendered cut ?? cut of the loaded edit; outside the source view this is exactly 016's `!previewStale` |
| `rerenderInFlight` | bool | true from the re-render's 202 until this clip's `ClipRendered` or `ClipRerenderFailed` |
| `videoVersion` | int | bumped on `ClipRendered`; cache-busts the result view's `src` |

State transitions of the re-render lock:

```text
idle --Re-render (save ok, 202)--> in_flight --ClipRendered(clip)--> idle (+ adopt event cut, videoVersion+1)
idle --Re-render (save fails)----> idle (error toast, nothing queued)
idle --Re-render (409)-----------> idle (server reason shown)
in_flight --ClipRerenderFailed(clip)--> idle (error shown, cut unchanged)
```

## 3. Source video (read-only resource)

| Attribute | Source |
|---|---|
| path | `JobState.video_path` (job record), never client input |
| exists | `retention.is_managed_file(path)` and `Path(path).is_file()` |
| removed | `retention.sweep_unused_sources` clears `video_path` once the job has no live clip (spec 001 FR-013) |
| duration | the preview's `<video>` metadata, else `JobState.duration` |

HTTP contract: [contracts/source-video.md](contracts/source-video.md).
