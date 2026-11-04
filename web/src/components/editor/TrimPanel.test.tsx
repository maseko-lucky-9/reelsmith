/**
 * The 016 Trim panel on the 017 editor (PR #97 re-review blocker): typed
 * segment times reach the timeline store through `replaceFromTimeline`, and
 * `setSegments` rounds them to the millisecond, so the panel checks the
 * values as written, and the store refuses an edit's new segments that break
 * the editor's 51 ms minimum or the server's segment rules. The server's
 * rules are ported on their own (`__fixtures__/serverRules.ts`: raw floats,
 * the source range clamp, the reel's end from the clamped segments).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import type { TimelinePayload } from "@/api/client";
import { segmentsOf } from "@/lib/timelineEdit";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { toastRefusal } from "@/editor/opencut/hooks/use-playback-controls";
import { TextPanel, TrimPanel } from "@/components/editor/EditorToolPanels";
import { serverRefusal } from "@/editor/__fixtures__/serverRules";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
import { toast } from "sonner";

const store = () => useTimelineStore.getState();

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

describe("Trim panel: a value that rounds to the time already there", () => {
  it("changes nothing and adds no undo step (start 50.2496 on [50.25, 51])", () => {
    mount([[50.25, 51]]);
    type(1, "start", "50.2496");
    expect(alertText()).toBeNull();
    expect(cut()).toEqual([{ start: 50.25, end: 51 }]);
    expect(store().history).toEqual([]);
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
      const unchanged = ms(Number(value)) === before[segment - 1][field];
      if (alertText() === null && !unchanged) {
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

/** The panel on the store, re-rendered with every change (as the route does). */
function LiveTrimPanel({ sourceDuration }: { sourceDuration: number | null }) {
  useTimelineStore((s) => s._tracks);
  return (
    <TrimPanel
      timeline={store().toTimeline()}
      onChange={(next) => toastRefusal(store().replaceFromTimeline(next))}
      playhead={0}
      sourceWindow={null}
      sourceDuration={sourceDuration}
    />
  );
}

function mountLive(segments: Array<[number, number]>, sourceDuration: number) {
  cleanup();
  store().clearTimeline();
  expect(
    store().load(timelineOf(segments), { sourceWindow: null, sourceDuration })
      .ok,
  ).toBe(true);
  return render(<LiveTrimPanel sourceDuration={sourceDuration} />);
}

describe("Trim panel: an end never past the source (PR #97 final review)", () => {
  it("repro A: source 124 s, [3.734, 124]: end 124.001 is refused, so start 123.95 leaves a segment the server takes", () => {
    mountLive([[3.734, 124]], 124);
    type(1, "end", "124.001");
    expect(alertText()).toMatch(/source video is 124\.00 s long/);
    expect(cut()).toEqual([{ start: 3.734, end: 124 }]);

    type(1, "start", "123.95");
    expect(cut()).toEqual([{ start: 3.734, end: 124 }]);
    expect(alertText()).toMatch(/at least 0\.051 s/);
    type(1, "start", "123.949");
    expect(alertText()).toBeNull();
    expect(cut()).toEqual([{ start: 123.949, end: 124 }]);
    expect(serverRefusal(store().toTimeline(), 124)).toBeNull();
  });

  it("repro B: source 95 s, [94.236, 95]: end 95.001 is refused, so the Text panel's overlay ends on the server's reel", () => {
    mountLive([[94.236, 95]], 95);
    type(1, "end", "95.001");
    expect(alertText()).not.toBeNull();
    expect(cut()).toEqual([{ start: 94.236, end: 95 }]);

    cleanup();
    render(
      <TextPanel
        timeline={store().toTimeline()}
        onChange={(next) => toastRefusal(store().replaceFromTimeline(next))}
        playhead={0}
      />,
    );
    fireEvent.change(screen.getByLabelText("Text", { selector: "input" }), {
      target: { value: "hi" },
    });
    fireEvent.change(screen.getByLabelText("Length (s)"), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add text" }));

    const overlays = store()
      .toTimeline()
      .tracks.filter((t) => t.kind === "text-overlay")
      .flatMap((t) => t.items);
    expect(overlays.map((o) => [o.start, o.end])).toEqual([[0, 0.764]]);
    expect(serverRefusal(store().toTimeline(), 95)).toBeNull();
  });

  it.each([
    ["an ms source", 124],
    ["a source off the ms grid", 123.9996],
  ])(
    "%s: the last segment within 51 ms of the end, its end typed 1 ms and 0.5 ms past, then its start pulled to 51 ms: every state passes the server",
    (_label, source) => {
      for (const past of [0.001, 0.0005]) {
        const last = Math.floor(source * 1000) / 1000;
        // Ends 30 ms before the source's end.
        mountLive([[ms(last - 3), ms(last - 0.03)]], source);
        const steps: Array<() => ["start" | "end", string]> = [
          () => ["end", String(source + past)],
          () => ["end", String(ms(source + past))],
          () => ["start", String(ms(cut()[0].end - 0.051))],
          () => ["start", String(ms(cut()[0].end - 0.0505))],
          () => ["end", String(source)],
          () => ["start", String(ms(cut()[0].end - 0.051))],
        ];
        for (const step of steps) {
          const [field, value] = step();
          act(() => type(1, field, value));
          expect(
            serverRefusal(store().toTimeline(), source),
            `${source} + ${past}: ${field} ${value} -> ${JSON.stringify(cut())}`,
          ).toBeNull();
        }
      }
    },
  );

  it("takes a start edit on a segment whose end the timeline's trim rounded 0.4 ms past a source off the ms grid", () => {
    mountLive([[200, 211]], 211.9836);
    const id = store()._tracks.find((t) => t.role === "segments")!.elements[0]
      .id;
    act(() => {
      expect(store().trimSegment(id, { end: 500 }).ok).toBe(true);
    });
    expect(cut()).toEqual([{ start: 200, end: 211.984 }]);

    type(1, "start", "211.933");
    expect(alertText()).toBeNull();
    expect(cut()).toEqual([{ start: 211.933, end: 211.984 }]);
    expect(serverRefusal(store().toTimeline(), 211.9836)).toBeNull();
  });
});

describe("Trim panel: an emptied or unreadable field", () => {
  it.each(["", "   "])(
    "does not move the start to 0 s for %j: it says so and puts the time back",
    (value) => {
      mountLive([[100, 110]], 600);
      type(1, "start", value);
      expect(cut()).toEqual([{ start: 100, end: 110 }]);
      expect(store().history).toEqual([]);
      expect(alertText()).toMatch(/enter a time/i);
      expect(screen.getByLabelText("Segment 1 start")).toHaveValue(100);
    },
  );
});
