# Contract: timeline adapter (schema v2 to the editor and back)

**Spec**: FR-004, FR-005 | **Module**: `web/src/editor/adapter.ts` | **Proving test**: `web/src/editor/adapter.test.ts` | **Data model**: [data-model.md §2](../data-model.md)

The adapter is the only code that knows both shapes. The store, the components and the persistence hook speak editor state; the server speaks schema v2 (ADR-014). The round trip is the contract.

## Interface

```text
fromTimeline(timeline: TimelinePayload, ctx: AdapterContext): AdapterResult
  ctx = { sourceWindow: SourceWindow | null, sourceDuration: number | null }
  AdapterResult =
    | { ok: true,  state: EditorState, converted: 'none' | 'v1' | 'merged-video-tracks' }
    | { ok: false, reason: string }            # a v1 edit upgradeTimeline() cannot convert

toTimeline(state: EditorState): TimelinePayload # always schema_version 2

EditorState = { tracks: EditorTrack[], captionStyle: string | null | undefined,
                timelineOrigin: Omit<TimelinePayload, 'tracks'> }
```

`sourceDuration` null: the adapter uses the furthest segment end (data-model §2, R5).

## Guarantees

1. **Round trip.** For every schema v2 `t` with at most one `video` track: `toTimeline(fromTimeline(t, ctx).state)` deep-equals `t` (same tracks in the same order, same items, same top-level keys, unknown keys included, absent keys still absent).
2. **v1.** A v1 `t` is converted with `upgradeTimeline(t, ctx.sourceWindow)` (016) before mapping; `converted: 'v1'`. `toTimeline` of the result equals `upgradeTimeline(t, window)` (so opening it is not a change: the persistence hook compares against that form).
3. **Several video tracks.** Merged in track order into one `segments` track; `converted: 'merged-video-tracks'`; `toTimeline` emits one `video` track at the first video track's position.
4. **Changed values only are rewritten.** After a store action, an item's fields the action did not change are byte-equal to the loaded ones; changed times are multiples of 0.001 (`round3`), changed `x`/`y` have at most four decimals, a changed `font_size` is an integer in [6, 400].
5. **Roles never mix.** A caption element is written to a `caption` track, an overlay to a `text-overlay` track, a voice-over to an `audio` track, a segment to the `video` track, whatever track the user dragged in the editor (the store forbids cross-role moves; the adapter does not rely on it).
6. **`mute_source`.** The `segments` track's `muted` maps to `mute_source`; a timeline without `mute_source` keeps it absent until the user toggles it.
7. **No paths.** The adapter never invents an audio `src` or a video `src`; it writes back what the server sent.

## Corpus (fixtures in `web/src/editor/__fixtures__/timelines.ts`)

| Fixture | Exercises |
|---|---|
| `serverDefault` | `GET /clips/{id}/edit` default: one segment, captions with `words`, `is_default` stripped |
| `multiSegment` | 3 segments with float boundaries (12.5, 20.0), (30.0, 47.25), (50.1, 50.3) |
| `wordedCaptions` | captions whose text matches and does not match their `words` count; a caption `style` key |
| `styledOverlay` | overlays with and without `font_size`, `color`; `x: 0.1`, `y: 0.3` (values that do not survive a pixel round trip) |
| `voiceover` | `audio` item with `gain: 0.8` and a bare `src` |
| `topLevel` | `caption_style: "hormozi"`, `mute_source: true`; and one with `caption_style: null` |
| `unknownKeys` | an item key and a top-level key the editor does not know |
| `emptyTracks` | empty `caption` and `text-overlay` tracks, tracks in a non-default order |
| `v1Placeholder` | the 016 editor's v1 `EMPTY_TIMELINE` (zero-length placeholder) with a window |
| `v1Foreign` | a v1 video item with `src` other than `main` (cannot convert) |
| `twoVideoTracks` | two `video` tracks |

## Mutation checks (the test must go red)

- Recompute `end` as `startTime + duration` in `toTimeline` instead of using `origin`: the `multiSegment` and `styledOverlay` round trips fail.
- Drop unknown keys: `unknownKeys` fails.
- Emit tracks in sorted (OpenCut) order: `emptyTracks` fails.
- Write `mute_source: false` when absent: `topLevel` / `serverDefault` fail.
