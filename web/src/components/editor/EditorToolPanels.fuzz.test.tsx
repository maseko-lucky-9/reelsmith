/**
 * Differential property test (PR #97 final review): random edits through the
 * real timeline store, the real Trim panel and the tool panels' timeline
 * helpers, near the edges that matter (the 51 ms minimum, the source's end,
 * neighbours, the reel's end), and every state the editor would save
 * (`prepareForSave`) is checked against a port of the server's validator
 * (`__fixtures__/serverRules.ts`). The reviewer ran this shape against the
 * real Python `validate_timeline` (66 of 108,288 states refused before the
 * fix, none after); here it is bounded and seeded so it runs in a few seconds.
 */
import { describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { TimelinePayload } from "@/api/client";
import {
  addItem,
  prepareForSave,
  reelDuration,
  removeItem,
  segmentsOf,
  type SourceWindow,
} from "@/lib/timelineEdit";
import { serverRefusal } from "@/editor/__fixtures__/serverRules";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { TrimPanel } from "@/components/editor/EditorToolPanels";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const SEEDS = 300;
const STEPS = 30;

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
const store = () => useTimelineStore.getState();

/** A saved edit the server takes: segments (some 50 ms, the last often at the source's end), captions, overlays, a voice-over. */
function generate(rand: () => number) {
  const kind = rand();
  // Whole seconds, on the ms grid, or off it (a probed length).
  const d =
    kind < 0.4
      ? Math.floor(30 + rand() * 300)
      : kind < 0.7
        ? ms(30 + rand() * 300)
        : 30 + rand() * 300;
  const segs: Array<[number, number]> = [];
  let cursor = ms(rand() * 5);
  for (let i = 0, n = 1 + Math.floor(rand() * 5); i < n; i++) {
    const start = ms(cursor + (rand() < 0.4 ? 0 : rand() * 5));
    const end =
      rand() < 0.25 ? ms(start + 0.051) : ms(start + 0.2 + rand() * 20);
    if (end > d) break;
    segs.push([start, end]);
    cursor = end;
  }
  if (segs.length === 0) segs.push([0, 10]);
  if (rand() < 0.4) {
    const last = segs[segs.length - 1];
    const e = Math.floor(d * 1000) / 1000;
    if (e - last[0] >= 0.051) last[1] = e;
  }
  const reel = segs.reduce((sum, [a, b]) => sum + (b - a), 0);
  const overlays = [];
  for (let i = 0, n = Math.floor(rand() * 3); i < n; i++) {
    const s = ms(rand() * Math.max(0, reel - 0.2));
    const e = ms(Math.min(reel, s + 0.06 + rand() * 4));
    if (e - s >= 0.05)
      overlays.push({ start: s, end: e, text: `t${i}`, x: 0.5, y: 0.1 });
  }
  const tracks: TimelinePayload["tracks"] = [
    {
      kind: "video",
      items: segs.map(([start, end]) => ({ start, end, src: "main" })),
    },
    {
      kind: "caption",
      items:
        reel > 1
          ? [{ start: 0.2, end: ms(Math.min(reel, 1)), text: "hi", words: [] }]
          : [],
    },
    { kind: "text-overlay", items: overlays },
  ];
  if (rand() < 0.5) {
    const s = ms(rand() * Math.max(0, reel - 0.5));
    const e = ms(Math.min(reel, s + 0.1 + rand() * 3));
    if (e - s >= 0.05)
      tracks.push({
        kind: "audio",
        items: [{ start: s, end: e, src: "vo.wav", gain: 1 }],
      });
  }
  const window: SourceWindow = {
    start: segs[0][0],
    end: segs[segs.length - 1][1],
  };
  return {
    timeline: { schema_version: 2, tracks } as TimelinePayload,
    d,
    window,
  };
}

describe("every edit the editor makes saves as a timeline the server takes", () => {
  it(`${SEEDS} seeded sessions of ${STEPS} edits each`, () => {
    let states = 0;
    let typed = 0;
    for (let seed = 1; seed <= SEEDS; seed++) {
      const rand = mulberry32(seed);
      const { timeline, d, window } = generate(rand);
      cleanup();
      store().clearTimeline();
      expect(
        store().load(timeline, { sourceWindow: window, sourceDuration: d }).ok,
      ).toBe(true);
      const check = (op: string) => {
        const saved = prepareForSave(store().toTimeline(), window);
        states++;
        expect(
          serverRefusal(saved, d),
          `seed ${seed} after ${op}: source ${d}, ${JSON.stringify(segmentsOf(saved))}`,
        ).toBeNull();
      };
      check("load");
      const onChange = (next: TimelinePayload) => {
        store().replaceFromTimeline(next);
      };
      let playhead = 0;
      const panel = () => (
        <TrimPanel
          timeline={store().toTimeline()}
          onChange={onChange}
          playhead={playhead}
          sourceWindow={window}
          sourceDuration={d}
        />
      );
      const view = render(panel());
      for (let step = 0; step < STEPS; step++) {
        view.rerender(panel());
        const segs = segmentsOf(store().toTimeline());
        const segTrack = store()._tracks.find((t) => t.role === "segments")!;
        const reel = store().getReelDuration();
        const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)];
        const near = (x: number, spread = 0.06) =>
          Math.round((x + (rand() * 2 - 1) * spread) * 10000) / 10000;
        const r = rand();
        let op: string;
        if (r < 0.35) {
          const idx = Math.floor(rand() * segs.length);
          const field = rand() < 0.5 ? "start" : "end";
          const s = segs[idx];
          const value = pick([
            near(field === "start" ? s.end - 0.05 : s.start + 0.05),
            near(d, 0.003),
            pick([d + 0.001, d + 0.0005, ms(d) + 0.001]),
            near(
              field === "start"
                ? (segs[idx - 1]?.end ?? 0)
                : (segs[idx + 1]?.start ?? d),
            ),
            near(s[field], 0.002),
            near(field === "start" ? s.end - 0.051 : s.start + 0.051, 0.0015),
          ]);
          op = `type segment ${idx + 1} ${field} ${value}`;
          typed++;
          const input = screen.getByLabelText(`Segment ${idx + 1} ${field}`);
          fireEvent.change(input, { target: { value: String(value) } });
          fireEvent.blur(input);
        } else if (r < 0.4) {
          playhead = rand() * reel;
          op = `Split at playhead ${playhead}`;
          view.rerender(panel());
          const button = screen.getByText(
            "Split at playhead",
          ) as HTMLButtonElement;
          if (!button.disabled) fireEvent.click(button);
        } else if (r < 0.55) {
          const el = pick(segTrack.elements);
          const len = el.duration - el.trimStart - el.trimEnd;
          const off = pick([
            rand() * len,
            near(0.051, 0.002),
            near(len - 0.051, 0.002),
          ]);
          const fn = pick([
            "splitElement",
            "splitAndKeepLeft",
            "splitAndKeepRight",
          ] as const);
          op = `${fn} at ${off}`;
          store()[fn](segTrack.id, el.id, el.startTime + off);
        } else if (r < 0.7) {
          const idx = Math.floor(rand() * segs.length);
          const s = segs[idx];
          const edges =
            rand() < 0.5
              ? {
                  start: pick([
                    near(s.start, 2),
                    near(s.end - 0.051, 0.002),
                    near(segs[idx - 1]?.end ?? 0, 0.002),
                  ]),
                }
              : {
                  end: pick([
                    near(s.end, 2),
                    near(s.start + 0.051, 0.002),
                    near(d, 0.01),
                  ]),
                };
          op = `trimSegment ${idx + 1} ${JSON.stringify(edges)}`;
          store().trimSegment(segTrack.elements[idx].id, edges);
        } else if (r < 0.75) {
          op = "undo";
          store().undo();
        } else if (r < 0.8) {
          op = "redo";
          store().redo();
        } else if (r < 0.92) {
          // The Text panel's Add text: its end clamped to the editor's reel.
          const t = store().toTimeline();
          const editorReel = reelDuration(t);
          const s = Math.round(rand() * editorReel * 100) / 100;
          const e = Math.min(s + pick([3, 0.05, 30]), editorReel);
          op = `add text ${s}-${e}`;
          if (e - s >= 0.05)
            onChange(
              addItem(t, "text-overlay", {
                start: ms(s),
                end: ms(e),
                text: "x",
                x: 0.5,
                y: 0.1,
              }),
            );
        } else {
          op = "remove text";
          onChange(removeItem(store().toTimeline(), "text-overlay", 0));
        }
        check(op);
      }
    }
    // Not vacuous.
    expect(states).toBeGreaterThan(SEEDS * STEPS);
    expect(typed).toBeGreaterThan(500);
  }, 20000);
});
