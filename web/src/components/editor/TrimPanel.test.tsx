/**
 * The 016 Trim panel on the 017 editor (PR #97 re-review blocker): typed
 * segment times reach the timeline store through `replaceFromTimeline`, and
 * `setSegments` rounds them to the millisecond, so the panel checks the
 * values as written, and the store refuses an edit's new segments that break
 * the editor's 51 ms minimum or the server's segment rules. The server's
 * rule is ported here on its own (raw floats, `_segments` in
 * app/services/timeline_render_service.py), with its source range.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { TimelinePayload } from "@/api/client";
import { segmentsOf } from "@/lib/timelineEdit";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { toastRefusal } from "@/editor/opencut/hooks/use-playback-controls";
import { TrimPanel } from "@/components/editor/EditorToolPanels";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
import { toast } from "sonner";

const store = () => useTimelineStore.getState();

/** The server's v2 segment rules on raw floats, inside the source `[0, source]`. */
function serverRefusal(
  timeline: TimelinePayload,
  source: number | null,
): string | null {
  const items = timeline.tracks
    .filter((t) => t.kind === "video")
    .flatMap((t) => t.items);
  if (items.length > 100) return "too many video items";
  let total = 0;
  let prevEnd: number | null = null;
  for (const [i, it] of items.entries()) {
    let start = Number(it.start);
    let end = Number(it.end);
    if (start < 0 || end < 0) return `video[${i}]: negative`;
    if (!(end > start)) return `video[${i}]: end must be > start`;
    if (source !== null) {
      if (start < -1e-3 || end > source + 1e-3)
        return `video[${i}]: outside the source`;
      start = Math.max(start, 0);
      end = Math.min(end, source);
    }
    if (end - start < 0.05) return `video[${i}]: shorter than 0.05s`;
    if (prevEnd !== null && start < prevEnd - 1e-3)
      return `video[${i}]: overlap`;
    if (prevEnd !== null) start = Math.max(start, prevEnd);
    total += end - start;
    if (total > 1200) return "the edited reel is longer than 1200s";
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

const ms = (n: number) => Math.round(n * 1000) / 1000;

const timelineOf = (segments: Array<[number, number]>): TimelinePayload => ({
  schema_version: 2,
  tracks: [
    {
      kind: "video",
      items: segments.map(([start, end]) => ({ start, end, src: "main" })),
    },
    {
      kind: "caption",
      items: [{ start: 0, end: 0.04, text: "hi", words: [] }],
    },
  ],
});

/** The editor's wiring: the panel's timeline from the store, its changes back through it (the route's applyTimeline). */
function mount(
  segments: Array<[number, number]>,
  sourceDuration: number | null = 4000,
) {
  cleanup();
  store().clearTimeline();
  expect(
    store().load(timelineOf(segments), {
      sourceWindow: null,
      sourceDuration,
    }).ok,
  ).toBe(true);
  render(
    <TrimPanel
      timeline={store().toTimeline()}
      onChange={(next) => toastRefusal(store().replaceFromTimeline(next))}
      playhead={0}
      sourceWindow={null}
      sourceDuration={sourceDuration}
    />,
  );
}

function type(segment: number, field: "start" | "end", value: string) {
  const input = screen.getByLabelText(`Segment ${segment} ${field}`);
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
}

const cut = () => segmentsOf(store().toTimeline());
const alertText = () => screen.queryByRole("alert")?.textContent ?? null;

beforeEach(() => {
  vi.mocked(toast.error).mockClear();
});

afterEach(() => {
  cleanup();
});

describe("Trim panel: typed times as written", () => {
  it("refuses an end typed 50.3004 on [50.25, 51] (written 50.3: 0.0499... s), with the reason, and keeps the segment", () => {
    mount([[50.25, 51]]);
    type(1, "end", "50.3004");

    expect(alertText()).toMatch(/at least 0\.051 s after the start/);
    expect(cut()).toEqual([{ start: 50.25, end: 51 }]);
    expect(store().history).toEqual([]);
  });

  it("takes 51 ms: end 50.301 on [50.25, 51]", () => {
    mount([[50.25, 51]]);
    type(1, "end", "50.301");

    expect(alertText()).toBeNull();
    expect(cut()).toEqual([{ start: 50.25, end: 50.301 }]);
    expect(serverRefusal(store().toTimeline(), 4000)).toBeNull();
  });

  it("refuses a reel longer than 20 minutes (end 1300 on [0, 100])", () => {
    mount([[0, 100]]);
    type(1, "end", "1300");

    expect(alertText()).toMatch(/at most 20 minutes/);
    expect(cut()).toEqual([{ start: 0, end: 100 }]);
  });

  it("refuses an end past the source when its length is known, and takes one at its end", () => {
    mount([[0, 100]], 211.98);
    type(1, "end", "212");

    expect(alertText()).toMatch(/source video is 211\.98 s long/);
    expect(cut()).toEqual([{ start: 0, end: 100 }]);

    mount([[0, 100]], 211.98);
    type(1, "end", "211.98");
    expect(alertText()).toBeNull();
    expect(cut()).toEqual([{ start: 0, end: 211.98 }]);
  });

  it("checks the order as written: a start typed 0.0004 s before the previous end rounds onto it, and is taken", () => {
    mount([
      [10, 20],
      [30, 40],
    ]);
    type(2, "start", "19.9996");

    expect(alertText()).toBeNull();
    expect(cut()).toEqual([
      { start: 10, end: 20 },
      { start: 20, end: 40 },
    ]);
  });
});

describe("Trim panel: every typed value it takes is one the server takes", () => {
  it("over the ms grid, near the minimum, the source's end and the 20 minutes", () => {
    const rand = mulberry32(41);
    let taken = 0;
    for (let i = 0; i < 300; i++) {
      const source = ms(1500 + rand() * 500);
      const a = ms(100 + rand() * 300);
      const b = ms(a + 0.2 + rand() * 0.6);
      const c = ms(b + 1 + rand() * 5);
      const d = ms(c + 0.2 + rand() * 0.6);
      mount(
        [
          [a, b],
          [c, d],
        ],
        source,
      );
      const near = (x: number) =>
        String(Math.round((x + (rand() - 0.5) * 0.008) * 10000) / 10000);
      const kind = rand();
      let segment: 1 | 2 = 1;
      let field: "start" | "end" = "end";
      let value: string;
      if (kind < 0.25) {
        value = near(a + 0.05);
      } else if (kind < 0.5) {
        [segment, field, value] = [2, "start", near(d - 0.05)];
      } else if (kind < 0.65) {
        [segment, field, value] = [1, "end", near(c)];
      } else if (kind < 0.8) {
        [segment, field, value] = [2, "end", near(source)];
      } else {
        // The 20 minutes: segment 2 ending near 1200 s of reel.
        [segment, field, value] = [2, "end", near(c + 1200 - (b - a))];
      }
      const before = cut();
      type(segment, field, value);
      const context = `${JSON.stringify(before)} source ${source}: segment ${segment} ${field} = ${value} -> ${JSON.stringify(cut())} (${alertText()})`;
      expect(serverRefusal(store().toTimeline(), source), context).toBeNull();
      if (alertText() === null) {
        taken++;
        expect(cut(), context).not.toEqual(before);
      } else {
        expect(cut(), context).toEqual(before);
      }
    }
    // Not vacuous: many go through, many are refused.
    expect(taken).toBeGreaterThan(60);
    expect(taken).toBeLessThan(270);
  });
});

describe("the store refuses an edit's new segments the server would refuse (replaceFromTimeline)", () => {
  it("refuses [50.25, 50.3] from a tool panel, with the reason", () => {
    mount([[50.25, 51]]);
    const result = store().replaceFromTimeline(timelineOf([[50.25, 50.3]]));
    expect(result).toEqual({
      ok: false,
      reason: expect.stringMatching(/at least 0\.051 s long/),
    });
    expect(cut()).toEqual([{ start: 50.25, end: 51 }]);
  });

  it("refuses a reel over 20 minutes from a tool panel", () => {
    mount([[0, 100]]);
    expect(store().replaceFromTimeline(timelineOf([[0, 1300]])).ok).toBe(false);
  });

  it("keeps a saved 0.05 s segment: loading it, a caption edit around it, and a server answer with it all go through", () => {
    const saved = timelineOf([
      [10, 10.05],
      [20, 30],
    ]);
    store().clearTimeline();
    expect(
      store().load(saved, { sourceWindow: null, sourceDuration: 100 }).ok,
    ).toBe(true);
    expect(store().toTimeline()).toStrictEqual(saved);

    const captionEdit = structuredClone(saved);
    captionEdit.tracks[1].items[0].text = "hello";
    expect(store().replaceFromTimeline(captionEdit).ok).toBe(true);

    // Trimming the other segment keeps the 0.05 s one as it was saved.
    expect(
      store().replaceFromTimeline(
        timelineOf([
          [10, 10.05],
          [20, 25],
        ]),
      ).ok,
    ).toBe(true);
    store().undo();

    const answer = structuredClone(saved);
    answer.tracks.push({
      kind: "audio",
      items: [{ start: 0, end: 1, src: "vo.wav", gain: 1 }],
    });
    expect(store().replaceFromTimeline(answer, "server").ok).toBe(true);
    expect(cut()).toEqual([
      { start: 10, end: 10.05 },
      { start: 20, end: 30 },
    ]);
  });
});
