/**
 * Schema v2 <-> editor state (spec 017 FR-004, FR-005; contracts/timeline-adapter.md).
 * The round trip is the contract: an unchanged edit comes back exactly as
 * loaded, and only what the editor changed is rewritten (rounded).
 */
import { describe, expect, it } from "vitest";
import type {
  TimelineItem,
  TimelinePayload,
  TimelineTrack,
} from "@/api/client";
import { upgradeTimeline } from "@/lib/timelineEdit";
import { fromTimeline, toTimeline, OVERLAY_CANVAS } from "./adapter";
import type {
  AdapterContext,
  EditorElement,
  EditorState,
  TrackRole,
} from "./types";
import {
  ROUND_TRIP_V2,
  WINDOW,
  multiSegment,
  serverDefault,
  styledOverlay,
  topLevel,
  topLevelNullStyle,
  twoVideoTracks,
  unknownKeys,
  v1Foreign,
  v1Placeholder,
  voiceover,
  wordedCaptions,
} from "./__fixtures__/timelines";

const NO_CTX: AdapterContext = { sourceWindow: null, sourceDuration: null };

function load(t: TimelinePayload, ctx: AdapterContext = NO_CTX): EditorState {
  const result = fromTimeline(t, ctx);
  if (!result.ok) throw new Error(`expected ok, got: ${result.reason}`);
  return result.state;
}

function elementsOf(state: EditorState, role: TrackRole): EditorElement[] {
  return state.tracks.filter((t) => t.role === role).flatMap((t) => t.elements);
}

/** The state with `fn` applied to the `index`th element of the first `role` track. */
function edit(
  state: EditorState,
  role: TrackRole,
  index: number,
  fn: (el: EditorElement) => EditorElement,
): EditorState {
  const trackIdx = state.tracks.findIndex((t) => t.role === role);
  return {
    ...state,
    tracks: state.tracks.map((t, i) =>
      i === trackIdx
        ? {
            ...t,
            elements: t.elements.map((el, j) => (j === index ? fn(el) : el)),
          }
        : t,
    ),
  };
}

const itemsOf = (
  t: TimelinePayload,
  kind: TimelineTrack["kind"],
): TimelineItem[] =>
  t.tracks.filter((k) => k.kind === kind).flatMap((k) => k.items);

describe("round trip (guarantee 1)", () => {
  it.each(Object.entries(ROUND_TRIP_V2))(
    "%s comes back exactly as loaded",
    (_name, t) => {
      const result = fromTimeline(t, NO_CTX);
      expect(result.ok).toBe(true);
      if (!result.ok) return;
      expect(result.converted).toBe("none");
      expect(toTimeline(result.state)).toStrictEqual(t);
    },
  );

  it("holds with a known source duration longer than the segments", () => {
    expect(
      toTimeline(
        load(multiSegment, { sourceWindow: null, sourceDuration: 3600.123 }),
      ),
    ).toStrictEqual(multiSegment);
  });

  it("holds for 300 generated schema v2 timelines", () => {
    const rand = mulberry32(17);
    for (let i = 0; i < 300; i++) {
      const { timeline, sourceDuration } = generateTimeline(rand);
      const state = load(timeline, { sourceWindow: null, sourceDuration });
      expect(
        toTimeline(state),
        `case ${i}: ${JSON.stringify(timeline)}`,
      ).toStrictEqual(timeline);
    }
  });
});

describe("schema v1 (guarantee 2)", () => {
  it.each([
    ["serverDefault", serverDefault],
    ["v1Placeholder", v1Placeholder],
  ])(
    "%s opens as upgradeTimeline(t, window) and reports converted: v1",
    (_name, t) => {
      const result = fromTimeline(t, {
        sourceWindow: WINDOW,
        sourceDuration: null,
      });
      expect(result.ok).toBe(true);
      if (!result.ok) return;
      expect(result.converted).toBe("v1");
      expect(toTimeline(result.state)).toStrictEqual(
        upgradeTimeline(t, WINDOW),
      );
    },
  );

  it("opens the placeholder as one segment spanning the clip's window", () => {
    const state = load(v1Placeholder, {
      sourceWindow: WINDOW,
      sourceDuration: null,
    });
    const [segment] = elementsOf(state, "segments");
    expect(elementsOf(state, "segments")).toHaveLength(1);
    expect(segment).toMatchObject({
      trimStart: 100,
      trimEnd: 0,
      duration: 110,
      startTime: 0,
    });
  });

  it("refuses a v1 video item reading another file, with a reason", () => {
    const result = fromTimeline(v1Foreign, {
      sourceWindow: WINDOW,
      sourceDuration: null,
    });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toMatch(/another file/i);
  });

  it("refuses a v1 edit when the clip's window is unknown, with a reason", () => {
    const result = fromTimeline(serverDefault, NO_CTX);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toMatch(/unknown/i);
  });
});

describe("several video tracks (guarantee 3)", () => {
  it("merges them in order onto one segments track at the first one’s position", () => {
    const result = fromTimeline(twoVideoTracks, NO_CTX);
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.converted).toBe("merged-video-tracks");
    expect(result.state.tracks.map((t) => t.role)).toEqual([
      "segments",
      "captions",
    ]);
    expect(
      elementsOf(result.state, "segments").map((el) => [
        el.trimStart,
        el.startTime,
      ]),
    ).toEqual([
      [10, 0],
      [20, 5],
    ]);
    expect(toTimeline(result.state)).toStrictEqual({
      schema_version: 2,
      tracks: [
        {
          kind: "video",
          items: [
            { start: 10, end: 15, src: "main" },
            { start: 20, end: 24, src: "main" },
          ],
        },
        twoVideoTracks.tracks[1],
      ],
    });
  });
});

describe("mapping (data-model §2)", () => {
  it("makes each segment a window of the whole source, back to back on the reel", () => {
    const state = load(multiSegment, {
      sourceWindow: null,
      sourceDuration: 60,
    });
    const track = state.tracks[0];
    expect(track).toMatchObject({
      role: "segments",
      type: "media",
      isMain: true,
      muted: false,
    });
    expect(
      track.elements.map((el) => [
        el.duration,
        el.trimStart,
        el.trimEnd,
        el.startTime,
      ]),
    ).toEqual([
      [60, 12.5, 40, 0],
      [60, 30, 12.75, 7.5],
      [60, 50.1, 60 - 50.3, 7.5 + 17.25],
    ]);
    expect(
      track.elements.every(
        (el) => el.type === "media" && el.mediaId === "main",
      ),
    ).toBe(true);
  });

  it("bounds the source by the furthest segment end when its duration is unknown or shorter", () => {
    for (const sourceDuration of [null, 10]) {
      const segments = elementsOf(
        load(multiSegment, { sourceWindow: null, sourceDuration }),
        "segments",
      );
      expect(segments.map((el) => el.duration)).toEqual([50.3, 50.3, 50.3]);
      expect(segments[2].trimEnd).toBe(0);
    }
  });

  it("maps captions, overlays (pixels from the centre on 1080x1920) and voice-overs", () => {
    const captions = elementsOf(load(wordedCaptions), "captions");
    expect(captions[0]).toMatchObject({
      type: "text",
      content: "matching words",
      startTime: 0.5,
      trimStart: 0,
    });

    const overlays = elementsOf(load(styledOverlay), "overlays");
    expect(OVERLAY_CANVAS).toEqual({ width: 1080, height: 1920 });
    expect(overlays[0]).toMatchObject({
      content: "plain",
      fontSize: 36,
      color: "#ffffff",
    });
    expect(overlays[0].type === "text" && overlays[0].x).toBeCloseTo(
      (0.1 - 0.5) * 1080,
      9,
    );
    expect(overlays[0].type === "text" && overlays[0].y).toBeCloseTo(
      (0.3 - 0.5) * 1920,
      9,
    );
    expect(overlays[1]).toMatchObject({
      fontSize: 72,
      color: "#ffcc00",
      x: 0,
      y: (0.1 - 0.5) * 1920,
    });
    // Absent x/y take the defaults 0.5 and 0.1.
    expect(overlays[2]).toMatchObject({ x: 0, y: (0.1 - 0.5) * 1920 });

    const [vo] = elementsOf(load(voiceover), "voiceovers");
    expect(vo).toMatchObject({
      type: "media",
      mediaId: "voiceover-1a2b3c.wav",
      startTime: 0.5,
      duration: 2.75,
    });
  });

  it("keeps the caption style and maps mute_source to the segments track", () => {
    const styled = load(topLevel);
    expect(styled.captionStyle).toBe("hormozi");
    expect(styled.tracks[0].muted).toBe(true);
    expect(load(topLevelNullStyle).captionStyle).toBeNull();
    expect(load(multiSegment).captionStyle).toBeUndefined();
  });
});

describe("write-back (guarantee 4: changed values only, rounded)", () => {
  it("rounds a moved overlay’s times to ms and keeps every other field byte-equal", () => {
    const state = edit(load(styledOverlay), "overlays", 0, (el) => ({
      ...el,
      startTime: 1.23456789,
    }));
    const [moved, styled, bare] = itemsOf(toTimeline(state), "text-overlay");
    expect(moved).toStrictEqual({
      start: 1.235,
      end: 1.585,
      text: "plain",
      x: 0.1,
      y: 0.3,
    });
    expect(styled).toBe(styledOverlay.tracks[1].items[1]);
    expect(bare).toStrictEqual({ start: 5, end: 6, text: "no position" });
  });

  it("rounds a resized overlay’s end only", () => {
    const state = edit(load(styledOverlay), "overlays", 1, (el) => ({
      ...el,
      duration: 2.00049,
    }));
    const [, resized] = itemsOf(toTimeline(state), "text-overlay");
    expect(resized).toStrictEqual({
      ...styledOverlay.tracks[1].items[1],
      end: 3.1,
    });
  });

  it("writes a changed position to four decimals and a font size as an integer in [6, 400]", () => {
    let state = edit(load(styledOverlay), "overlays", 0, (el) =>
      el.type === "text"
        ? { ...el, x: 123.456789, fontSize: 500, color: "#00ff00" }
        : el,
    );
    state = edit(state, "overlays", 2, (el) =>
      el.type === "text" ? { ...el, y: -500, fontSize: 6.4 } : el,
    );
    const [first, , third] = itemsOf(toTimeline(state), "text-overlay");
    expect(first).toStrictEqual({
      start: 0.1,
      end: 0.45,
      text: "plain",
      x: 0.6143,
      y: 0.3,
      font_size: 400,
      color: "#00ff00",
    });
    expect(third).toStrictEqual({
      start: 5,
      end: 6,
      text: "no position",
      y: 0.2396,
      font_size: 6,
    });
    expect(String(first.x).split(".")[1].length).toBeLessThanOrEqual(4);
  });

  it("writes a caption’s new text and keeps its words and style", () => {
    const state = edit(load(wordedCaptions), "captions", 0, (el) =>
      el.type === "text" ? { ...el, content: "matching birds" } : el,
    );
    const [caption, other] = itemsOf(toTimeline(state), "caption");
    expect(caption).toStrictEqual({
      ...wordedCaptions.tracks[1].items[0],
      text: "matching birds",
    });
    expect(other).toBe(wordedCaptions.tracks[1].items[1]);
  });

  it("writes a trimmed segment’s changed edge only, rounded, and never a src it did not have", () => {
    let state = load(styledOverlay, { sourceWindow: null, sourceDuration: 30 });
    state = edit(state, "segments", 0, (el) => ({ ...el, trimStart: 2.0004 }));
    expect(itemsOf(toTimeline(state), "video")).toStrictEqual([
      { start: 2, end: 10 },
    ]);

    state = edit(state, "segments", 0, (el) => ({
      ...el,
      trimEnd: 30 - 12.3456,
    }));
    expect(itemsOf(toTimeline(state), "video")).toStrictEqual([
      { start: 2, end: 12.346 },
    ]);
  });

  it("writes mute_source only once toggled, and the caption style when changed", () => {
    const base = load(multiSegment);
    const muted = {
      ...base,
      tracks: base.tracks.map((t, i) => (i === 0 ? { ...t, muted: true } : t)),
    };
    expect(toTimeline(muted).mute_source).toBe(true);
    const unmuted = {
      ...muted,
      tracks: muted.tracks.map((t, i) =>
        i === 0 ? { ...t, muted: false } : t,
      ),
    };
    expect("mute_source" in toTimeline(unmuted)).toBe(false);

    expect(toTimeline({ ...base, captionStyle: "mrbeast" }).caption_style).toBe(
      "mrbeast",
    );
    expect(
      toTimeline({ ...base, captionStyle: null }).caption_style,
    ).toBeNull();
    expect(
      "caption_style" in
        toTimeline({ ...load(topLevel), captionStyle: undefined }),
    ).toBe(false);
  });
});

describe("roles never mix (guarantee 5)", () => {
  it("writes an overlay dragged onto a caption track to a text-overlay track", () => {
    const base = load({
      schema_version: 2,
      tracks: [...styledOverlay.tracks, { kind: "caption", items: [] }],
    });
    const overlay = base.tracks[1].elements[0];
    const state: EditorState = {
      ...base,
      tracks: base.tracks.map((t) =>
        t.role === "overlays"
          ? { ...t, elements: t.elements.slice(1) }
          : t.role === "captions"
            ? { ...t, elements: [overlay] }
            : t,
      ),
    };
    const out = toTimeline(state);
    expect(itemsOf(out, "caption")).toEqual([]);
    expect(itemsOf(out, "text-overlay").map((it) => it.text)).toEqual([
      "plain",
      "styled\nline two",
      "no position",
    ]);
  });
});

describe("no paths (guarantee 7)", () => {
  it("writes back the server’s audio src even if the element’s mediaId changed", () => {
    const state = edit(load(voiceover), "voiceovers", 0, (el) =>
      el.type === "media" ? { ...el, mediaId: "../../etc/passwd" } : el,
    );
    expect(itemsOf(toTimeline(state), "audio")).toStrictEqual(
      voiceover.tracks[2].items,
    );
  });

  it("keeps unknown keys on items, tracks and the top level", () => {
    const state = edit(load(unknownKeys), "captions", 0, (el) =>
      el.type === "text" ? { ...el, content: "hey" } : el,
    );
    const out = toTimeline(state) as TimelinePayload & {
      editor_hint?: unknown;
    };
    expect(out.editor_hint).toEqual({ zoom: 2 });
    expect(out.tracks[1]).toMatchObject({
      kind: "caption",
      label: "Speaker A",
    });
    expect(out.tracks[1].items[0]).toMatchObject({
      text: "hey",
      future: { a: 1 },
    });
  });
});

// ── generated corpus ────────────────────────────────────────────────────────

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

function generateTimeline(rand: () => number): {
  timeline: TimelinePayload;
  sourceDuration: number | null;
} {
  const int = (lo: number, hi: number) =>
    lo + Math.floor(rand() * (hi - lo + 1));
  const num = (lo: number, hi: number) => {
    const places = int(0, 6);
    return Math.round((lo + rand() * (hi - lo)) * 10 ** places) / 10 ** places;
  };
  const pick = <T>(xs: T[]) => xs[int(0, xs.length - 1)];

  const segments: TimelineItem[] = [];
  let cursor = num(0, 20);
  for (let i = 0, n = int(1, 5); i < n; i++) {
    const start = cursor + num(0, 5);
    const end = start + 0.05 + num(0, 10);
    segments.push(rand() < 0.7 ? { start, end, src: "main" } : { start, end });
    cursor = end;
  }
  const reel = segments.reduce((sum, s) => sum + (s.end - s.start), 0);

  const span = () => {
    const start = num(0, reel);
    return { start, end: start + num(0.05, 5) };
  };
  const captions = Array.from({ length: int(0, 4) }, () => {
    const s = span();
    const words = Array.from({ length: int(0, 3) }, (_, k) => ({
      word: `w${k}`,
      start: s.start,
      end: s.end,
    }));
    const item: TimelineItem = {
      ...s,
      text: words.map((w) => w.word).join(" ") || "x",
      words,
    };
    if (rand() < 0.3) item.style = "default";
    return item;
  });
  const overlays = Array.from({ length: int(0, 3) }, () => {
    const item: TimelineItem = {
      ...span(),
      text: pick(["hook", "a\nb", "<b>x</b>"]),
    };
    if (rand() < 0.7) item.x = num(0, 1);
    if (rand() < 0.7) item.y = num(0, 1);
    if (rand() < 0.5) item.font_size = int(6, 400);
    if (rand() < 0.5) item.color = pick(["#ffffff", "#ff0000", "yellow"]);
    return item;
  });
  const audio = Array.from({ length: int(0, 2) }, (_, k) => ({
    ...span(),
    src: `vo-${k}.wav`,
    gain: num(0, 4),
  }));

  const tracks: TimelineTrack[] = [{ kind: "video", items: segments }];
  if (captions.length || rand() < 0.5)
    tracks.push({ kind: "caption", items: captions });
  if (overlays.length || rand() < 0.5)
    tracks.push({ kind: "text-overlay", items: overlays });
  if (audio.length || rand() < 0.3)
    tracks.push({ kind: "audio", items: audio });
  if (rand() < 0.2) tracks.push({ kind: "caption", items: [] });
  for (let i = tracks.length - 1; i > 0; i--) {
    const j = int(0, i);
    [tracks[i], tracks[j]] = [tracks[j], tracks[i]];
  }
  if (rand() < 0.2)
    (tracks[0] as TimelineTrack & { label?: string }).label = "extra";

  const timeline: TimelinePayload & Record<string, unknown> = {
    schema_version: 2,
    tracks,
  };
  const style = rand();
  if (style < 0.3)
    timeline.caption_style = pick(["hormozi", "karaoke", "subtle"]);
  else if (style < 0.45) timeline.caption_style = null;
  const mute = rand();
  if (mute < 0.3) timeline.mute_source = true;
  else if (mute < 0.45) timeline.mute_source = false;
  if (rand() < 0.2) timeline.future_key = { v: int(0, 9) };

  const furthest = Math.max(...segments.map((s) => s.end));
  const sourceDuration = rand() < 0.5 ? null : furthest + num(0, 100);
  return { timeline, sourceDuration };
}
