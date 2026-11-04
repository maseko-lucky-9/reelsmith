/**
 * The rendered-cut comparison that gates Split on the rendered clip's clock
 * (spec 016 FR-017, kept by spec 017): boundaries within 1 ms are the same
 * cut, `[]` is "unknown" and never matches, null is the clip's own window.
 */
import { describe, expect, it } from "vitest";
import type { TimelinePayload } from "@/api/client";
import {
  CUT_TOLERANCE,
  RERENDER_IN_FLIGHT_REASON,
  STALE_PLAYHEAD_REASON,
  cutOf,
  renderedCut,
  sameCut,
  splitGate,
} from "./renderedCut";

const WINDOW = { start: 100, end: 110 };

describe("sameCut", () => {
  it("matches boundaries 1 ms apart", () => {
    expect(
      sameCut([{ start: 101.5, end: 110 }], [{ start: 101.501, end: 109.999 }]),
    ).toBe(true);
  });

  it("matches a raw float boundary against the server nudging it by 1 ms", () => {
    // The server rounds 101.0045 to 101.005 or nudges it to 101.0055.
    expect(
      sameCut([{ start: 101.0045, end: 110 }], [{ start: 101.0055, end: 110 }]),
    ).toBe(true);
    expect(
      sameCut([{ start: 101.0045, end: 110 }], [{ start: 101.0035, end: 110 }]),
    ).toBe(true);
  });

  it("does not match boundaries 1.1 ms apart, at either edge", () => {
    expect(
      sameCut([{ start: 101.0045, end: 110 }], [{ start: 101.0056, end: 110 }]),
    ).toBe(false);
    expect(
      sameCut([{ start: 101, end: 110 }], [{ start: 101, end: 110.0011 }]),
    ).toBe(false);
  });

  it("never matches a different number of segments", () => {
    const one = [{ start: 100, end: 110 }];
    expect(sameCut(one, [...one, { start: 120, end: 130 }])).toBe(false);
    expect(sameCut([], one)).toBe(false);
  });

  it("has a tolerance of 1 ms plus float slack", () => {
    expect(CUT_TOLERANCE).toBeCloseTo(1e-3, 8);
    expect(CUT_TOLERANCE).toBeGreaterThan(1e-3);
  });
});

describe("renderedCut", () => {
  it("reads the server's [[start, end], ...] pairs as a cut", () => {
    expect(
      renderedCut(
        [
          [101.5, 105],
          [106, 110],
        ],
        WINDOW,
      ),
    ).toEqual([
      { start: 101.5, end: 105 },
      { start: 106, end: 110 },
    ]);
  });

  it("is 'unknown' for [] (a render is replacing the file), even with a window", () => {
    expect(renderedCut([], WINDOW)).toBe("unknown");
  });

  it("is the clip's own window when nothing is recorded", () => {
    expect(renderedCut(null, WINDOW)).toEqual([WINDOW]);
    expect(renderedCut(undefined, WINDOW)).toEqual([WINDOW]);
  });

  it("is null when nothing is recorded and there is no window", () => {
    expect(renderedCut(null, null)).toBeNull();
  });
});

describe("cutOf", () => {
  it("reads a v1 timeline through the clip's window", () => {
    const v1: TimelinePayload = {
      tracks: [
        {
          kind: "video",
          items: [{ start: 0, end: 10, src: "main", trim_start: 1.5 }],
        },
      ],
    };
    expect(cutOf(v1, WINDOW)).toEqual([{ start: 101.5, end: 110 }]);
  });

  it("is a v2 timeline's segments", () => {
    const v2: TimelinePayload = {
      schema_version: 2,
      tracks: [
        {
          kind: "video",
          items: [
            { start: 12.5, end: 20, src: "main" },
            { start: 30, end: 47.25 },
          ],
        },
      ],
    };
    expect(cutOf(v2, null)).toEqual([
      { start: 12.5, end: 20 },
      { start: 30, end: 47.25 },
    ]);
  });

  it("is empty for a v1 timeline with no window (it cannot be read)", () => {
    const v1: TimelinePayload = {
      tracks: [{ kind: "video", items: [{ start: 0, end: 10, src: "main" }] }],
    };
    expect(cutOf(v1, null)).toEqual([]);
  });
});

describe("splitGate", () => {
  const trimmed = [{ start: 101.5, end: 110 }];
  const open = { ok: true };
  const stale = { ok: false, reason: STALE_PLAYHEAD_REASON };

  it("is always open in the clean-source view (the playhead is the edit's clock)", () => {
    expect(
      splitGate({ view: "source", edit: trimmed, baseline: [WINDOW] }),
    ).toEqual(open);
    expect(
      splitGate({ view: "source", edit: trimmed, baseline: "unknown" }),
    ).toEqual(open);
    expect(
      splitGate({
        view: "source",
        edit: trimmed,
        baseline: "unknown",
        rerenderInFlight: true,
      }),
    ).toEqual(open);
  });

  it("in the rendered-clip view, opens only on the cut the file was rendered from", () => {
    expect(
      splitGate({ view: "result", edit: [WINDOW], baseline: [WINDOW] }),
    ).toEqual(open);
    expect(
      splitGate({ view: "result", edit: trimmed, baseline: [WINDOW] }),
    ).toEqual(stale);
    expect(
      splitGate({
        view: "result",
        edit: [{ start: 100.001, end: 110 }],
        baseline: [WINDOW],
      }),
    ).toEqual(open);
  });

  it("never opens on an unknown rendered cut ([]: a render is replacing the file)", () => {
    expect(
      splitGate({ view: "result", edit: [WINDOW], baseline: "unknown" }),
    ).toEqual(stale);
  });

  it("is open when there is no cut to compare (no window, nothing recorded)", () => {
    expect(splitGate({ view: "result", edit: trimmed, baseline: null })).toEqual(
      open,
    );
  });

  it("waits for a re-render in flight in the rendered-clip view, even on the same cut", () => {
    expect(
      splitGate({
        view: "result",
        edit: [WINDOW],
        baseline: [WINDOW],
        rerenderInFlight: true,
      }),
    ).toEqual({ ok: false, reason: RERENDER_IN_FLIGHT_REASON });
  });
});
