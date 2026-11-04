/**
 * The preview's clock and layout (spec 017 T152, FR-013, FR-014): reel time
 * to source time through the segments, the boundaries playback seeks at, the
 * caption word the render highlights, and the reel_geometry mirror checked
 * against the fixture pytest checks against Python (research R11).
 */
import { describe, expect, it } from "vitest";
import cases from "./reel-geometry-cases.json";
import {
  activeWordIndex,
  captionWords,
  nextBoundary,
  reelTimeAt,
  segmentIndexAt,
  sourceTimeAt,
} from "./previewClock";
import { fontScale, reelGeometry } from "./previewLayout";

const SEGMENTS = [
  { start: 10, end: 14 },
  { start: 20, end: 25 },
];

describe("previewClock", () => {
  it("maps reel time to source time through the segments", () => {
    expect(sourceTimeAt(SEGMENTS, 0)).toBe(10);
    expect(sourceTimeAt(SEGMENTS, 2.5)).toBe(12.5);
    expect(sourceTimeAt(SEGMENTS, 5)).toBe(21);
  });

  it("continues at the next segment's start at a boundary", () => {
    expect(sourceTimeAt(SEGMENTS, 4)).toBe(20);
    expect(nextBoundary(SEGMENTS, 3)).toEqual({
      index: 0,
      reel: 4,
      sourceEnd: 14,
      continueAt: 20,
    });
    expect(nextBoundary(SEGMENTS, 4)).toEqual({
      index: 1,
      reel: 9,
      sourceEnd: 25,
      continueAt: null,
    });
  });

  it("is null past the reel (and before it, and without segments)", () => {
    expect(sourceTimeAt(SEGMENTS, 9.5)).toBeNull();
    expect(segmentIndexAt(SEGMENTS, 9.5)).toBeNull();
    expect(nextBoundary(SEGMENTS, 9.5)).toBeNull();
    expect(sourceTimeAt(SEGMENTS, -1)).toBeNull();
    expect(sourceTimeAt([], 0)).toBeNull();
  });

  it("shows the last segment's end at the reel's last instant", () => {
    expect(sourceTimeAt(SEGMENTS, 9)).toBe(25);
    expect(segmentIndexAt(SEGMENTS, 9)).toBe(1);
  });

  it("gives the segment playing at a reel time; a boundary belongs to the segment it starts", () => {
    expect(segmentIndexAt(SEGMENTS, 0)).toBe(0);
    expect(segmentIndexAt(SEGMENTS, 3.9)).toBe(0);
    expect(segmentIndexAt(SEGMENTS, 4)).toBe(1);
    expect(segmentIndexAt(SEGMENTS, 8.5)).toBe(1);
  });

  it("maps a source time back to the reel, null outside the segments", () => {
    expect(reelTimeAt(SEGMENTS, 12)).toBe(2);
    expect(reelTimeAt(SEGMENTS, 21)).toBe(5);
    expect(reelTimeAt(SEGMENTS, 14)).toBe(4);
    expect(reelTimeAt(SEGMENTS, 16)).toBeNull();
    expect(reelTimeAt(SEGMENTS, 5)).toBeNull();
    expect(reelTimeAt(SEGMENTS, 26)).toBeNull();
  });
});

describe("caption words (timeline_render_service._caption_words)", () => {
  const raw = [
    { word: "one", start: 1, end: 1.4 },
    { word: "two", start: 1.5, end: 1.9 },
    { word: "three", start: 2, end: 2.6 },
  ];

  it("keeps each word's timing, clamped to the caption, when the word count matches", () => {
    expect(captionWords("Uno  dos\ttres", raw, 1.2, 2.5)).toEqual([
      { text: "Uno", start: 1.2, end: 1.4 },
      { text: "dos", start: 1.5, end: 1.9 },
      { text: "tres", start: 2, end: 2.5 },
    ]);
  });

  it("spreads the words evenly over the caption when the count changed", () => {
    expect(captionWords("a b c d", raw, 1, 3)).toEqual([
      { text: "a", start: 1, end: 1.5 },
      { text: "b", start: 1.5, end: 2 },
      { text: "c", start: 2, end: 2.5 },
      { text: "d", start: 2.5, end: 3 },
    ]);
  });

  it("has no words for blank text", () => {
    expect(captionWords("   ", raw, 1, 3)).toEqual([]);
  });
});

describe("activeWordIndex (render_service.plan_caption_entries)", () => {
  const words = [
    { text: "one", start: 1.5, end: 1.8 },
    { text: "two", start: 2, end: 2.2 },
    { text: "three", start: 2.5, end: 2.7 },
  ];

  it("highlights a word from its start until the next word's start, the last until the caption ends", () => {
    expect(activeWordIndex(words, 3, 1.5, true)).toBe(0);
    expect(activeWordIndex(words, 3, 1.9, true)).toBe(0);
    expect(activeWordIndex(words, 3, 2, true)).toBe(1);
    expect(activeWordIndex(words, 3, 2.9, true)).toBe(2);
  });

  it("shows the caption plain before its first word", () => {
    expect(activeWordIndex(words, 3, 1.2, true)).toBeNull();
  });

  it("never highlights for a look that does not highlight", () => {
    expect(activeWordIndex(words, 3, 2, false)).toBeNull();
  });
});

describe("previewLayout (clip_service.reel_geometry mirror)", () => {
  it("covers landscape and portrait sources", () => {
    const sizes = cases.map((c) => c.source_size.join("x"));
    expect(sizes).toEqual(
      expect.arrayContaining([
        "1920x1080",
        "576x1024",
        "1080x1920",
        "1080x2340",
      ]),
    );
  });

  it.each(cases.map((c) => [c.source_size.join("x"), c] as const))(
    "%s matches reel_geometry",
    (_name, c) => {
      const g = reelGeometry(c.source_size[0], c.source_size[1]);
      expect({
        source_size: g.sourceSize,
        canvas_size: g.canvasSize,
        band_anchor_y: g.bandAnchorY,
        inset_height: g.insetHeight,
      }).toEqual(c);
    },
  );

  it("gives 1920x1080 a 1080x1920 canvas, a 608 px inset and the band middle 1592", () => {
    expect(reelGeometry(1920, 1080)).toEqual({
      sourceSize: [1920, 1080],
      canvasSize: [1080, 1920],
      insetHeight: 608,
      insetTop: 656,
      bandAnchorY: 1592,
    });
  });

  it("anchors a source taller than 9:16 at 85% of the canvas, its inset top above the canvas", () => {
    expect(reelGeometry(1080, 2340)).toEqual({
      sourceSize: [1080, 2340],
      canvasSize: [1080, 1920],
      insetHeight: 2340,
      insetTop: -210,
      bandAnchorY: 1632,
    });
    // Python floors // : (1137 - 1200) // 2 is -32, not -31.
    expect(reelGeometry(640, 1200).insetTop).toBe(-32);
  });

  it("scales fonts by preview width / canvas width", () => {
    expect(fontScale(270, reelGeometry(1920, 1080))).toBe(0.25);
    expect(fontScale(288, reelGeometry(576, 1024))).toBe(0.5);
  });
});
