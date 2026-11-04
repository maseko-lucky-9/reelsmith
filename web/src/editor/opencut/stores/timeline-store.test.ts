/**
 * The adapted OpenCut timeline store (spec 017 T144; FR-006..FR-009, FR-012).
 * Segment edits go through the 016 rules (`timelineEdit.splitAt` /
 * `setSegments`), so captions, overlays and voice-overs follow the source;
 * every change is one undoable snapshot; refusals change nothing.
 */
import { beforeEach, describe, expect, it } from "vitest";
import type { TimelineItem, TimelinePayload } from "@/api/client";
import { setCaptionStyle, splitAt } from "@/lib/timelineEdit";
import type { EditorElement, EditorTrack, TrackRole } from "@/editor/types";
import {
  multiSegment,
  v1Foreign,
  WINDOW,
} from "@/editor/__fixtures__/timelines";
import { HISTORY_LIMIT, useTimelineStore } from "./timeline-store";

const store = () => useTimelineStore.getState();

/** One segment (no `src`), a caption off the ms grid, an overlay and a voice-over. */
const SINGLE: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start: 100, end: 110 }] },
    {
      kind: "caption",
      items: [
        {
          start: 1.2345,
          end: 2,
          text: "hello there",
          words: [
            { word: "hello", start: 1.2345, end: 1.6 },
            { word: "there", start: 1.6, end: 2 },
          ],
        },
        {
          start: 6,
          end: 7,
          text: "later",
          words: [{ word: "later", start: 6, end: 7 }],
        },
      ],
    },
    {
      kind: "text-overlay",
      items: [{ start: 3, end: 4.5, text: "hook", x: 0.5, y: 0.2 }],
    },
    {
      kind: "audio",
      items: [{ start: 0.5, end: 4.8, src: "vo.wav", gain: 1 }],
    },
  ],
};

/** Two segments; captions in the first 2 s, later in segment 1, and in segment 2. */
const TWO: TimelinePayload = {
  schema_version: 2,
  tracks: [
    {
      kind: "video",
      items: [
        { start: 100, end: 110, src: "main" },
        { start: 120, end: 130, src: "main" },
      ],
    },
    {
      kind: "caption",
      items: [
        { start: 0.5, end: 1.5, text: "cut away", words: [] },
        { start: 5, end: 6, text: "stays", words: [] },
        { start: 12, end: 13, text: "second", words: [] },
      ],
    },
  ],
};

function trackOf(role: TrackRole): EditorTrack {
  const track = store()._tracks.find((t) => t.role === role);
  if (!track) throw new Error(`no ${role} track`);
  return track;
}
const elementOf = (role: TrackRole, i: number): EditorElement =>
  trackOf(role).elements[i];
const itemsOf = (t: TimelinePayload, kind: string): TimelineItem[] =>
  t.tracks.filter((k) => k.kind === kind).flatMap((k) => k.items);
const spans = (t: TimelinePayload, kind: string) =>
  itemsOf(t, kind).map((it) => [it.start, it.end]);

function load(t: TimelinePayload, sourceDuration: number | null = null) {
  const result = store().load(t, { sourceWindow: WINDOW, sourceDuration });
  expect(result.ok).toBe(true);
}

beforeEach(() => {
  store().clearTimeline();
});

describe("load", () => {
  it("opens a v2 edit with an empty history and writes it back unchanged", () => {
    load(multiSegment);
    expect(store().history).toEqual([]);
    expect(store().redoStack).toEqual([]);
    expect(store().readOnly).toBeNull();
    expect(store().toTimeline()).toStrictEqual(multiSegment);
  });

  it("clears the history of the previous edit", () => {
    load(SINGLE);
    store().splitElement(
      trackOf("segments").id,
      elementOf("segments", 0).id,
      4,
    );
    expect(store().history).toHaveLength(1);
    load(TWO);
    expect(store().history).toEqual([]);
  });

  it("opens a v1 edit it cannot convert read-only, with the reason, and writes it back unchanged", () => {
    const result = store().load(v1Foreign, {
      sourceWindow: WINDOW,
      sourceDuration: null,
    });
    expect(result.ok).toBe(false);
    expect(store().readOnly).toMatch(/another file/i);
    expect(store().toTimeline()).toBe(v1Foreign);
    // Shown on the timeline all the same.
    expect(trackOf("captions").elements).toHaveLength(1);

    const split = store().splitElement(
      trackOf("segments").id,
      elementOf("segments", 0).id,
      2.5,
    );
    expect(split).toEqual({ ok: false, reason: store().readOnly });
    expect(store().history).toEqual([]);
  });
});

describe("split (FR-008)", () => {
  it("splits mid-segment into two and leaves captions, overlays and voice-overs where they were", () => {
    load(SINGLE);
    const before = store().toTimeline();
    const segment = elementOf("segments", 0);
    store().selectElement(trackOf("segments").id, segment.id);

    const result = store().splitElement(trackOf("segments").id, segment.id, 4);

    expect(result.ok).toBe(true);
    const after = store().toTimeline();
    // The 016 rule exactly (timelineEdit.splitAt), not a per-element split.
    expect(after).toStrictEqual(splitAt(before, 4));
    expect(spans(after, "video")).toEqual([
      [100, 104],
      [104, 110],
    ]);
    const close = (a: TimelinePayload, b: TimelinePayload, kind: string) =>
      itemsOf(a, kind).forEach((it, i) => {
        expect(it.start).toBeCloseTo(itemsOf(b, kind)[i].start, 2);
        expect(it.end).toBeCloseTo(itemsOf(b, kind)[i].end, 2);
      });
    close(after, before, "caption");
    close(after, before, "text-overlay");
    close(after, before, "audio");
    expect(itemsOf(after, "caption")).toHaveLength(2);
    // The left part keeps the id (and stays selected); the right part is new.
    expect(trackOf("segments").elements.map((el) => el.id)).toEqual([
      segment.id,
      result.ok && result.id,
    ]);
    expect(store().selectedElements).toEqual([
      { trackId: trackOf("segments").id, elementId: segment.id },
    ]);
    expect(store().history).toHaveLength(1);
  });

  it("keeps only the left or only the right part", () => {
    load(SINGLE);
    expect(
      store().splitAndKeepLeft(
        trackOf("segments").id,
        elementOf("segments", 0).id,
        4,
      ).ok,
    ).toBe(true);
    expect(spans(store().toTimeline(), "video")).toEqual([[100, 104]]);
    // The voice-over keeps its start and is cut at the new reel end; the later caption is gone.
    expect(spans(store().toTimeline(), "audio")).toEqual([[0.5, 4]]);
    expect(itemsOf(store().toTimeline(), "caption").map((c) => c.text)).toEqual(
      ["hello there"],
    );

    store().undo();
    expect(
      store().splitAndKeepRight(
        trackOf("segments").id,
        elementOf("segments", 0).id,
        4,
      ).ok,
    ).toBe(true);
    expect(spans(store().toTimeline(), "video")).toEqual([[104, 110]]);
    expect(spans(store().toTimeline(), "caption")).toEqual([[2, 3]]);
  });
});

describe("trim and extend (FR-006, FR-007)", () => {
  it("trimming the head 2 s moves later captions 2 s earlier and drops captions whose speech was cut", () => {
    load(TWO, 200);
    const result = store().trimSegment(elementOf("segments", 0).id, {
      start: 102,
    });
    expect(result.ok).toBe(true);
    const t = store().toTimeline();
    expect(spans(t, "video")).toEqual([
      [102, 110],
      [120, 130],
    ]);
    expect(itemsOf(t, "caption").map((c) => [c.text, c.start, c.end])).toEqual([
      ["stays", 3, 4],
      ["second", 10, 11],
    ]);
  });

  it("extends up to the source end and no further", () => {
    load(SINGLE, 115);
    expect(
      store().trimSegment(elementOf("segments", 0).id, { end: 130 }).ok,
    ).toBe(true);
    expect(spans(store().toTimeline(), "video")).toEqual([[100, 115]]);
  });

  it("extends up to the next segment's source start and no further", () => {
    load(TWO, 200);
    expect(
      store().trimSegment(elementOf("segments", 0).id, { end: 125 }).ok,
    ).toBe(true);
    expect(
      store().trimSegment(elementOf("segments", 1).id, { start: 90 }).ok,
    ).toBe(true);
    expect(spans(store().toTimeline(), "video")).toEqual([
      [100, 120],
      [120, 130],
    ]);
  });

  it("bounds an extension by the furthest segment end until the source duration is known, then by it", () => {
    load(SINGLE);
    store().trimSegment(elementOf("segments", 0).id, { end: 120 });
    expect(spans(store().toTimeline(), "video")).toEqual([[100, 110]]);
    expect(store().history).toEqual([]);

    store().setSourceDuration(118.5, "probe");
    expect(store().history).toEqual([]);
    expect(store().toTimeline()).toStrictEqual(SINGLE);
    store().trimSegment(elementOf("segments", 0).id, { end: 120 });
    expect(spans(store().toTimeline(), "video")).toEqual([[100, 118.5]]);
  });

  it("keeps the source's own length (the preview's metadata) over the job's recorded one, whichever comes first", () => {
    // The reviewer's order: the <video> metadata, then the edit, then the job (yt-dlp's 212 s).
    store().setSourceDuration(211.98, "probe");
    load(SINGLE, null);
    expect(store().sourceDuration).toBe(211.98);
    store().setSourceDuration(212, "job");
    expect(store().sourceDuration).toBe(211.98);

    const id = elementOf("segments", 0).id;
    expect(store().trimSegment(id, { end: 400 }).ok).toBe(true);
    expect(spans(store().toTimeline(), "video")).toEqual([[100, 211.98]]);
  });

  it("takes the job's length until the source's metadata arrives, then the metadata's", () => {
    load(SINGLE, 212);
    expect(store().sourceDuration).toBe(212);
    store().setSourceDuration(211.98, "probe");
    expect(store().sourceDuration).toBe(211.98);
    store().setSourceDuration(213, "job");
    expect(store().sourceDuration).toBe(211.98);
    // A new editor forgets the old source's metadata.
    store().clearTimeline();
    load(SINGLE, 212);
    expect(store().sourceDuration).toBe(212);
  });

  it("deletes a segment, and the captions on it with it", () => {
    load(TWO, 200);
    expect(store().deleteSegment(elementOf("segments", 0).id).ok).toBe(true);
    const t = store().toTimeline();
    expect(spans(t, "video")).toEqual([[120, 130]]);
    expect(itemsOf(t, "caption").map((c) => [c.text, c.start, c.end])).toEqual([
      ["second", 2, 3],
    ]);
  });
});

describe("refusals change nothing and push no history", () => {
  const refusals: Array<[string, () => { ok: boolean }]> = [
    [
      "split within 0.05 s of an edge",
      () =>
        store().splitElement(
          trackOf("segments").id,
          elementOf("segments", 0).id,
          0.03,
        ),
    ],
    [
      "split a caption",
      () =>
        store().splitElement(
          trackOf("captions").id,
          elementOf("captions", 0).id,
          1.5,
        ),
    ],
    [
      "split with the playhead outside the segment",
      () =>
        store().splitElement(
          trackOf("segments").id,
          elementOf("segments", 0).id,
          12,
        ),
    ],
    [
      "delete the last segment",
      () => store().deleteSegment(elementOf("segments", 0).id),
    ],
    [
      "remove the last segment from its track",
      () =>
        store().removeElementFromTrack(
          trackOf("segments").id,
          elementOf("segments", 0).id,
        ),
    ],
    [
      "trim a segment below 0.05 s",
      () => store().trimSegment(elementOf("segments", 0).id, { start: 109.98 }),
    ],
    [
      "drag a segment along the reel",
      () =>
        store().updateElementStartTime(
          trackOf("segments").id,
          elementOf("segments", 0).id,
          2,
        ),
    ],
    [
      "start dragging a segment",
      () =>
        store().startDrag(
          elementOf("segments", 0).id,
          trackOf("segments").id,
          0,
          0,
          0,
        ),
    ],
    [
      "move a caption in time",
      () =>
        store().updateElementStartTime(
          trackOf("captions").id,
          elementOf("captions", 0).id,
          3,
        ),
    ],
    [
      "resize a caption",
      () =>
        store().updateElementDuration(
          trackOf("captions").id,
          elementOf("captions", 0).id,
          3,
        ),
    ],
    [
      "resize a voice-over",
      () =>
        store().updateElementDuration(
          trackOf("voiceovers").id,
          elementOf("voiceovers", 0).id,
          1,
        ),
    ],
    [
      "move an overlay onto a caption track",
      () =>
        store().moveElementToTrack(
          trackOf("overlays").id,
          trackOf("captions").id,
          elementOf("overlays", 0).id,
        ),
    ],
    [
      "move a segment onto the voice-over track",
      () =>
        store().moveElementToTrack(
          trackOf("segments").id,
          trackOf("voiceovers").id,
          elementOf("segments", 0).id,
        ),
    ],
    [
      "mute a caption track",
      () => store().toggleTrackMute(trackOf("captions").id),
    ],
    [
      "give a caption a font size",
      () =>
        store().updateTextElement(
          trackOf("captions").id,
          elementOf("captions", 0).id,
          { content: "x", fontSize: 80 },
        ),
    ],
    [
      "give an overlay an 11th line break",
      () =>
        store().updateTextElement(
          trackOf("overlays").id,
          elementOf("overlays", 0).id,
          {
            content: Array.from({ length: 12 }, (_, i) => `l${i}`).join("\n"),
          },
        ),
    ],
    [
      "give an overlay 501 characters",
      () =>
        store().updateTextElement(
          trackOf("overlays").id,
          elementOf("overlays", 0).id,
          { content: "x".repeat(501) },
        ),
    ],
    [
      "rotate an overlay",
      () =>
        store().updateTextElement(
          trackOf("overlays").id,
          elementOf("overlays", 0).id,
          { rotation: 45 },
        ),
    ],
  ];

  it.each(refusals)("refuses to %s, with a reason", (_name, act) => {
    load(SINGLE);
    const before = store().toTimeline();
    const dragBefore = store().dragState;
    const result = act() as { ok: boolean; reason?: string };
    expect(result.ok).toBe(false);
    expect(result.reason).toEqual(expect.any(String));
    expect(store().toTimeline()).toStrictEqual(before);
    expect(store().history).toEqual([]);
    expect(store().dragState).toBe(dragBefore);
  });
});

describe("overlays, voice-overs and captions (FR-009)", () => {
  it("moves an overlay and a voice-over in time, clamped to [0, reel end]", () => {
    load(SINGLE);
    expect(
      store().updateElementStartTime(
        trackOf("overlays").id,
        elementOf("overlays", 0).id,
        9.5,
      ).ok,
    ).toBe(true);
    expect(spans(store().toTimeline(), "text-overlay")).toEqual([[8.5, 10]]);
    store().updateElementStartTime(
      trackOf("voiceovers").id,
      elementOf("voiceovers", 0).id,
      -2,
    );
    expect(spans(store().toTimeline(), "audio")).toEqual([[0, 4.3]]);
    expect(store().history).toHaveLength(2);
  });

  it("resizes an overlay up to the reel end", () => {
    load(SINGLE);
    store().updateElementDuration(
      trackOf("overlays").id,
      elementOf("overlays", 0).id,
      20,
    );
    expect(spans(store().toTimeline(), "text-overlay")).toEqual([[3, 10]]);
  });

  it("edits a caption's text and an overlay's text, size, colour and position", () => {
    load(SINGLE);
    store().updateTextElement(
      trackOf("captions").id,
      elementOf("captions", 0).id,
      { content: "hello world" },
    );
    store().updateTextElement(
      trackOf("overlays").id,
      elementOf("overlays", 0).id,
      {
        content: "new hook",
        fontSize: 500,
        color: "#ff0000",
        x: 270,
      },
    );
    const t = store().toTimeline();
    expect(itemsOf(t, "caption")[0]).toStrictEqual({
      ...itemsOf(SINGLE, "caption")[0],
      text: "hello world",
    });
    expect(itemsOf(t, "text-overlay")[0]).toStrictEqual({
      start: 3,
      end: 4.5,
      text: "new hook",
      x: 0.75,
      y: 0.2,
      font_size: 400,
      color: "#ff0000",
    });
  });

  it("deletes a caption (a click selects, Delete removes)", () => {
    load(SINGLE);
    store().selectElement(trackOf("captions").id, elementOf("captions", 0).id);
    expect(trackOf("captions").elements).toHaveLength(2);
    store().removeElementFromTrack(
      trackOf("captions").id,
      elementOf("captions", 0).id,
    );
    expect(itemsOf(store().toTimeline(), "caption").map((c) => c.text)).toEqual(
      ["later"],
    );
    expect(store().selectedElements).toEqual([]);
  });

  it("mutes the source from the segments track", () => {
    load(SINGLE);
    store().toggleTrackMute(trackOf("segments").id);
    expect(store().toTimeline().mute_source).toBe(true);
  });
});

describe("undo and redo (FR-012)", () => {
  it("restores whole snapshots, caption style included", () => {
    load(SINGLE);
    const original = store().toTimeline();
    store().replaceFromTimeline(setCaptionStyle(original, "hormozi"));
    const styled = store().toTimeline();
    store().splitElement(
      trackOf("segments").id,
      elementOf("segments", 0).id,
      4,
    );
    const split = store().toTimeline();

    store().undo();
    expect(store().toTimeline()).toStrictEqual(styled);
    expect(store().captionStyle).toBe("hormozi");
    store().undo();
    expect(store().toTimeline()).toStrictEqual(original);
    expect(store().captionStyle).toBeUndefined();
    store().undo();
    expect(store().toTimeline()).toStrictEqual(original);

    store().redo();
    expect(store().toTimeline()).toStrictEqual(styled);
    store().redo();
    expect(store().toTimeline()).toStrictEqual(split);
    // Redo keeps the step undoable.
    store().undo();
    expect(store().toTimeline()).toStrictEqual(styled);
  });

  it("a new change clears redo", () => {
    load(SINGLE);
    store().toggleTrackMute(trackOf("segments").id);
    store().undo();
    expect(store().redoStack).toHaveLength(1);
    store().updateElementStartTime(
      trackOf("overlays").id,
      elementOf("overlays", 0).id,
      1,
    );
    expect(store().redoStack).toEqual([]);
  });

  it(`keeps ${HISTORY_LIMIT} undo steps after 51 changes`, () => {
    load(SINGLE);
    for (let i = 1; i <= 51; i++) {
      store().updateElementStartTime(
        trackOf("overlays").id,
        elementOf("overlays", 0).id,
        i / 10,
      );
    }
    expect(HISTORY_LIMIT).toBe(50);
    expect(store().history).toHaveLength(50);
    for (let i = 0; i < 60; i++) store().undo();
    // The oldest kept state is after the first change, not the loaded one.
    expect(spans(store().toTimeline(), "text-overlay")).toEqual([[0.1, 1.6]]);
  });

  it("replaceFromTimeline is one undoable step", () => {
    load(TWO, 200);
    const before = store().toTimeline();
    const next = setCaptionStyle(before, "karaoke");
    expect(store().replaceFromTimeline(next).ok).toBe(true);
    expect(store().history).toHaveLength(1);
    expect(store().toTimeline()).toStrictEqual(next);
    store().undo();
    expect(store().toTimeline()).toStrictEqual(before);
  });
});
