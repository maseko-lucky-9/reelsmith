/**
 * The adapter's round-trip corpus (spec 017 contracts/timeline-adapter.md).
 * Each fixture is a timeline as the server stores or sends it; the values
 * are chosen to break any adapter that recomputes instead of writing back
 * (float boundaries, pixel positions that do not survive a round trip,
 * absent keys, unknown keys, a non-default track order).
 */
import type { TimelinePayload } from "@/api/client";
import { NO_EDIT_TIMELINE } from "@/editor/adapter";

/** The clip's window in its source (ClipRecord start/end) for the v1 fixtures. */
export const WINDOW = { start: 100, end: 110 };

/**
 * `GET /clips/{id}/edit` with no saved edit (app/services/clip_words.default_timeline):
 * schema v1, the whole reel as one video item, captions with their words.
 * `is_default` lives on the edit state, not the timeline, so it is not here.
 */
export const serverDefault: TimelinePayload = {
  tracks: [
    {
      kind: "video",
      items: [{ start: 0, end: 10, src: "main", trim_start: 0 }],
    },
    {
      kind: "caption",
      items: [
        {
          start: 0.24,
          end: 1.1,
          text: "hello there",
          style: "default",
          words: [
            { word: "hello", start: 0.24, end: 0.6 },
            { word: "there", start: 0.6, end: 1.1 },
          ],
        },
        {
          start: 5.5,
          end: 6.25,
          text: "later",
          style: "default",
          words: [{ word: "later", start: 5.5, end: 6.25 }],
        },
      ],
    },
    { kind: "text-overlay", items: [] },
  ],
};

export const multiSegment: TimelinePayload = {
  schema_version: 2,
  tracks: [
    {
      kind: "video",
      items: [
        { start: 12.5, end: 20.0, src: "main" },
        { start: 30.0, end: 47.25, src: "main" },
        { start: 50.1, end: 50.3, src: "main" },
      ],
    },
    {
      kind: "caption",
      items: [
        {
          // 0.65 + (1.8 - 0.65) is 1.7999999999999998: recomputing `end` breaks the round trip.
          start: 0.65,
          end: 1.8,
          text: "one two",
          words: [
            { word: "one", start: 0.65, end: 1.2 },
            { word: "two", start: 1.2, end: 1.8 },
          ],
        },
        {
          start: 8,
          end: 9.75,
          text: "three",
          words: [{ word: "three", start: 8, end: 9.75 }],
        },
        {
          start: 24.75,
          end: 24.95,
          text: "four",
          words: [{ word: "four", start: 24.75, end: 24.95 }],
        },
      ],
    },
    { kind: "text-overlay", items: [] },
  ],
};

export const wordedCaptions: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start: 40, end: 50, src: "main" }] },
    {
      kind: "caption",
      items: [
        {
          start: 0.5,
          end: 1.7,
          text: "matching words",
          style: "default",
          words: [
            { word: "matching", start: 0.5, end: 1.1 },
            { word: "words", start: 1.1, end: 1.7 },
          ],
        },
        {
          start: 2,
          end: 3.3,
          text: "now three words",
          words: [
            { word: "two", start: 2, end: 2.6 },
            { word: "words", start: 2.6, end: 3.3 },
          ],
        },
      ],
    },
  ],
};

export const styledOverlay: TimelinePayload = {
  schema_version: 2,
  tracks: [
    // No `src`: the adapter must not invent one.
    { kind: "video", items: [{ start: 0, end: 10 }] },
    {
      kind: "text-overlay",
      items: [
        // 0.1 + (0.45 - 0.1) is 0.44999999999999996, and (0.1 - 0.5) * 1080 / 1080 + 0.5 is not 0.1.
        { start: 0.1, end: 0.45, text: "plain", x: 0.1, y: 0.3 },
        {
          start: 1.1,
          end: 4.7,
          text: "styled\nline two",
          x: 0.5,
          y: 0.1,
          font_size: 72,
          color: "#ffcc00",
        },
        { start: 5, end: 6, text: "no position" },
      ],
    },
  ],
};

export const voiceover: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start: 100, end: 110, src: "main" }] },
    { kind: "caption", items: [] },
    {
      kind: "audio",
      items: [
        { start: 0.5, end: 3.25, src: "voiceover-1a2b3c.wav", gain: 0.8 },
      ],
    },
  ],
};

export const topLevel: TimelinePayload = {
  schema_version: 2,
  caption_style: "hormozi",
  mute_source: true,
  tracks: [{ kind: "video", items: [{ start: 100, end: 110, src: "main" }] }],
};

/** `caption_style: null` (the default look, stated) and no `mute_source`. */
export const topLevelNullStyle: TimelinePayload = {
  schema_version: 2,
  caption_style: null,
  tracks: [{ kind: "video", items: [{ start: 100, end: 110, src: "main" }] }],
};

export const unknownKeys = {
  schema_version: 2,
  editor_hint: { zoom: 2 },
  tracks: [
    {
      kind: "video",
      items: [{ start: 100, end: 110, src: "main", future_flag: true }],
    },
    {
      kind: "caption",
      label: "Speaker A",
      items: [
        {
          start: 1,
          end: 2,
          text: "hi",
          words: [{ word: "hi", start: 1, end: 2, conf: 0.93 }],
          future: { a: 1 },
        },
      ],
    },
  ],
} as TimelinePayload;

/** Empty caption and text-overlay tracks, in an order OpenCut would sort differently. */
export const emptyTracks: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: "audio", items: [] },
    { kind: "caption", items: [] },
    { kind: "video", items: [{ start: 3, end: 8.5, src: "main" }] },
    { kind: "text-overlay", items: [] },
  ],
};

/** The 016 editor's "no edit yet" v1 placeholder (a zero-length video item), opened with WINDOW. */
export const v1Placeholder: TimelinePayload = NO_EDIT_TIMELINE;

/** A v1 video item reading another file: upgradeTimeline cannot convert it. */
export const v1Foreign: TimelinePayload = {
  tracks: [
    {
      kind: "video",
      items: [{ start: 0, end: 5, src: "broll.mp4", trim_start: 0 }],
    },
    {
      kind: "caption",
      items: [{ start: 1, end: 2, text: "kept", style: "default" }],
    },
  ],
};

export const twoVideoTracks: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start: 10, end: 15, src: "main" }] },
    {
      kind: "caption",
      items: [
        {
          start: 1,
          end: 2,
          text: "a",
          words: [{ word: "a", start: 1, end: 2 }],
        },
      ],
    },
    { kind: "video", items: [{ start: 20, end: 24, src: "main" }] },
  ],
};

/** Schema v2 fixtures with at most one video track: each must round-trip exactly. */
export const ROUND_TRIP_V2: Record<string, TimelinePayload> = {
  multiSegment,
  wordedCaptions,
  styledOverlay,
  voiceover,
  topLevel,
  topLevelNullStyle,
  unknownKeys,
  emptyTracks,
};
