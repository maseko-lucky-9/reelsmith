/**
 * Every segment edit the editor makes is one the server accepts (PR #97
 * review, blocker B). The server refuses a segment when `end - start < 0.05`
 * in raw float math (app/services/timeline_render_service.py `_segments`),
 * and times are rounded to the millisecond, so a 50 ms span such as
 * 50.25..50.3 is 0.04999999999999716 there. The rule is ported here on its
 * own (not imported from the code under test) and checked over the ms grid
 * for split, split keep left / right, trim and the edge drag's clamps.
 */
import type React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, renderHook } from "@testing-library/react";
import type { TimelinePayload } from "@/api/client";
import { segmentsOf, splitAt } from "@/lib/timelineEdit";
import { useTimelineElementResize } from "@/editor/opencut/hooks/use-timeline-element-resize";
import { useTimelineStore } from "./timeline-store";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const store = () => useTimelineStore.getState();

/** The server's segment rules, as `_segments` applies them (raw floats, in order). */
function serverRefusal(timeline: TimelinePayload): string | null {
  const items = timeline.tracks
    .filter((t) => t.kind === "video")
    .flatMap((t) => t.items);
  if (items.length === 0) return "no segment";
  if (items.length > 100) return "too many video items";
  let total = 0;
  let prevEnd: number | null = null;
  for (const [i, it] of items.entries()) {
    let start = Number(it.start);
    const end = Number(it.end);
    if (!(end > start)) return `video[${i}]: end must be > start`;
    if (end - start < 0.05) return `video[${i}]: shorter than 0.05s`;
    if (prevEnd !== null && start < prevEnd - 1e-3)
      return `video[${i}]: overlap`;
    if (prevEnd !== null) start = Math.max(start, prevEnd);
    total += end - start;
    if (total > 20 * 60) return "the edited reel is longer than 1200s";
    prevEnd = end;
  }
  return null;
}

function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A value on the millisecond grid, as the editor writes and the server stores times. */
const ms = (n: number) => Math.round(n * 1000) / 1000;

const oneSegment = (start: number, end: number): TimelinePayload => ({
  schema_version: 2,
  tracks: [{ kind: "video", items: [{ start, end, src: "main" }] }],
});

/** Three segments with room between them, the middle one under test. */
const threeSegments = (start: number, end: number): TimelinePayload => ({
  schema_version: 2,
  tracks: [
    {
      kind: "video",
      items: [
        { start: ms(start - 5), end: ms(start - 2), src: "main" },
        { start, end, src: "main" },
        { start: ms(end + 2), end: ms(end + 5), src: "main" },
      ],
    },
  ],
});

function load(timeline: TimelinePayload, sourceDuration = 4000) {
  store().clearTimeline();
  const result = store().load(timeline, {
    sourceWindow: null,
    sourceDuration,
  });
  expect(result.ok).toBe(true);
}

const segmentsTrack = () => {
  const track = store()._tracks.find((t) => t.role === "segments");
  if (!track) throw new Error("no segments track");
  return track;
};

/** Expects the edit as it stands to pass the server's rules. */
function expectAccepted(context: string) {
  expect(serverRefusal(store().toTimeline()), context).toBeNull();
}

beforeEach(() => {
  store().clearTimeline();
});

describe("the server's segment rule on raw floats", () => {
  it("refuses the reviewer's 50 ms span (the port is not vacuous)", () => {
    expect(50.3 - 50.25).toBeLessThan(0.05);
    expect(serverRefusal(oneSegment(50.25, 50.3))).toMatch(/shorter/);
    expect(serverRefusal(oneSegment(50.25, 50.301))).toBeNull();
  });
});

describe("split, split keep left and split keep right", () => {
  it("never produce a segment the server refuses, over the ms grid", () => {
    const rand = mulberry32(97);
    let applied = 0;
    for (let i = 0; i < 400; i++) {
      // A segment on the ms grid, 0.09 to 0.2 s long, somewhere in the first 1000 s.
      const start = ms(rand() * 1000);
      const end = ms(start + 0.09 + rand() * 0.11);
      const length = end - start;
      // The playhead 0.049 to 0.053 s from one edge, on a 0.1 ms grid.
      const near = Math.round((0.049 + rand() * 0.004) * 10000) / 10000;
      const t = rand() < 0.5 ? near : length - near;
      for (const mode of ["split", "keep-left", "keep-right"] as const) {
        load(oneSegment(start, end));
        const track = segmentsTrack();
        const id = track.elements[0].id;
        const result =
          mode === "split"
            ? store().splitElement(track.id, id, t)
            : mode === "keep-left"
              ? store().splitAndKeepLeft(track.id, id, t)
              : store().splitAndKeepRight(track.id, id, t);
        const context = `${mode} [${start}, ${end}] at ${t}: ${JSON.stringify(segmentsOf(store().toTimeline()))}`;
        if (result.ok) applied++;
        expectAccepted(context);
      }
    }
    // Not vacuous: most of these edits go through.
    expect(applied).toBeGreaterThan(400);
  });

  it("splits 51 ms from an edge and refuses 50 ms (the reviewer's 50.25..50.3)", () => {
    load(oneSegment(50.25, 50.5));
    const track = segmentsTrack();
    const id = track.elements[0].id;
    // 0.0504 s in: the cut rounds to 50.3, a 50 ms part.
    expect(store().splitAndKeepLeft(track.id, id, 0.0504).ok).toBe(false);
    expect(store().splitElement(track.id, id, 0.0504).ok).toBe(false);
    expect(segmentsOf(store().toTimeline())).toEqual([
      { start: 50.25, end: 50.5 },
    ]);
    expect(store().splitAndKeepLeft(track.id, id, 0.051).ok).toBe(true);
    expect(segmentsOf(store().toTimeline())).toEqual([
      { start: 50.25, end: 50.301 },
    ]);
  });

  it("splitAt (the 016 Trim panel's split) keeps the same minimum", () => {
    for (const t of [0.05, 0.0504, 0.0505, 0.2, 0.1996]) {
      const next = splitAt(oneSegment(50.25, 50.5), t);
      if (next) expect(serverRefusal(next), `at ${t}`).toBeNull();
    }
    expect(splitAt(oneSegment(50.25, 50.5), 0.0504)).toBeNull();
    expect(splitAt(oneSegment(50.25, 50.5), 0.051)).not.toBeNull();
  });
});

describe("trimSegment", () => {
  it("never leaves a segment the server refuses, over the ms grid", () => {
    const rand = mulberry32(5);
    let applied = 0;
    for (let i = 0; i < 400; i++) {
      const start = ms(10 + rand() * 1000);
      const end = ms(start + 0.2 + rand() * 0.5);
      load(threeSegments(start, end));
      const id = segmentsTrack().elements[1].id;
      // An edge 0.049 to 0.053 s from the other one, on a 0.1 ms grid.
      const gap = Math.round((0.049 + rand() * 0.004) * 10000) / 10000;
      const edges = rand() < 0.5 ? { start: end - gap } : { end: start + gap };
      if (store().trimSegment(id, edges).ok) applied++;
      expectAccepted(`trim [${start}, ${end}] to ${JSON.stringify(edges)}`);
    }
    expect(applied).toBeGreaterThan(100);
  });

  it("refuses the reviewer's 50.25..50.3 and keeps 50.25..50.301", () => {
    load(threeSegments(50.25, 51));
    const id = segmentsTrack().elements[1].id;
    expect(store().trimSegment(id, { end: 50.3 }).ok).toBe(false);
    expect(store().trimSegment(id, { end: 50.301 }).ok).toBe(true);
    expect(segmentsOf(store().toTimeline())[1]).toEqual({
      start: 50.25,
      end: 50.301,
    });
  });
});

describe("the server's caps (data-model §1)", () => {
  it("refuses a split past 100 segments, with the reason, and changes nothing", () => {
    const items = Array.from({ length: 100 }, (_, i) => ({
      start: i * 2,
      end: i * 2 + 1,
      src: "main",
    }));
    load({ schema_version: 2, tracks: [{ kind: "video", items }] });
    const track = segmentsTrack();
    const before = store().toTimeline();

    const result = store().splitElement(track.id, track.elements[0].id, 0.5);

    expect(result).toEqual({
      ok: false,
      reason: expect.stringMatching(/at most 100 segments/),
    });
    expect(store().toTimeline()).toStrictEqual(before);
    expect(store().history).toEqual([]);
    // Keep left / right replace the segment: still 100, allowed.
    expect(
      store().splitAndKeepLeft(track.id, track.elements[0].id, 0.5).ok,
    ).toBe(true);
  });

  it("refuses a trim that makes the reel longer than 20 minutes, and takes one that ends at 20", () => {
    load(oneSegment(0, 1199.5));
    const id = segmentsTrack().elements[0].id;

    const result = store().trimSegment(id, { end: 1201 });

    expect(result).toEqual({
      ok: false,
      reason: expect.stringMatching(/at most 20 minutes/),
    });
    expect(segmentsOf(store().toTimeline())).toEqual([
      { start: 0, end: 1199.5 },
    ]);
    expect(store().trimSegment(id, { end: 1200 }).ok).toBe(true);
    expectAccepted("a 1200 s reel");
  });

  it("counts every segment toward the 20 minutes", () => {
    load(threeSegments(10, 1190));
    const id = segmentsTrack().elements[2].id;
    // 3 + 1180 + 3 s is 1186 s: the last one to 1199 makes 1193 s, to 1212 makes 1203 s.
    expect(store().trimSegment(id, { end: 1199 }).ok).toBe(true);
    expect(store().trimSegment(id, { end: 1212 }).ok).toBe(false);
    expectAccepted("three segments under 20 minutes");
  });
});

describe("the edge drag", () => {
  const mouse = (clientX: number) =>
    ({
      button: 0,
      clientX,
      preventDefault: () => {},
      stopPropagation: () => {},
    }) as unknown as React.MouseEvent;

  /** Drags one edge of the middle segment far past the other edge and releases. */
  function dragPastTheOtherEdge(side: "left" | "right") {
    const track = segmentsTrack();
    const element = track.elements[1];
    const { result, unmount } = renderHook(() =>
      useTimelineElementResize({ element, track, zoomLevel: 1 }),
    );
    act(() => result.current.handleResizeStart(mouse(0), element.id, side));
    // 5000 px at 50 px/s is 100 s: the clamp holds the edge at the minimum length.
    const x = side === "left" ? 5000 : -5000;
    act(() => {
      fireEvent.mouseMove(document, { clientX: x });
    });
    act(() => {
      fireEvent.mouseUp(document, { clientX: x });
    });
    unmount();
  }

  it.each(["left", "right"] as const)(
    "clamps the %s edge to a length the server accepts, and the commit goes through",
    (side) => {
      const rand = mulberry32(side === "left" ? 1 : 2);
      for (let i = 0; i < 60; i++) {
        const start = i === 0 ? 50.25 : ms(10 + rand() * 1000);
        const end = ms(start + 0.25 + rand() * 0.5);
        load(threeSegments(start, end));
        dragPastTheOtherEdge(side);
        const context = `${side} edge of [${start}, ${end}]: ${JSON.stringify(segmentsOf(store().toTimeline()))}`;
        // Committed (one undo step), not refused at the server's limit.
        expect(store().history, context).toHaveLength(1);
        expectAccepted(context);
        const seg = segmentsOf(store().toTimeline())[1];
        expect(seg.end - seg.start, context).toBeLessThan(0.052);
      }
    },
  );
});
