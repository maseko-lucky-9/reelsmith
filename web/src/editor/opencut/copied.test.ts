/**
 * The OpenCut files copied verbatim (spec 017 T141) still behave as upstream:
 * time codes, frame snapping, and the playback store's seek.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatTimeCode } from "./lib/time";
import {
  snapTimeToFrame,
  getCumulativeHeightBefore,
  TRACK_HEIGHTS,
} from "./constants/timeline-constants";
import { usePlaybackStore } from "./stores/playback-store";
import {
  ensureMainTrack,
  sortTracksByOrder,
  type TimelineTrack,
} from "./types/timeline";

describe("formatTimeCode", () => {
  it("formats in each of its formats", () => {
    expect(formatTimeCode(3725.5)).toBe("01:02:05:50");
    expect(formatTimeCode(65.25, "MM:SS")).toBe("01:05");
    expect(formatTimeCode(3725, "HH:MM:SS")).toBe("01:02:05");
    expect(formatTimeCode(1.5, "HH:MM:SS:FF", 30)).toBe("00:00:01:15");
  });
});

describe("snapTimeToFrame", () => {
  it("snaps to the nearest 1/30 s at 30 fps", () => {
    expect(snapTimeToFrame(1.01, 30)).toBeCloseTo(1, 10);
    expect(snapTimeToFrame(1.02, 30)).toBeCloseTo(31 / 30, 10);
    expect(snapTimeToFrame(0.5, 30)).toBeCloseTo(15 / 30, 10);
  });

  it("returns the time unchanged for an invalid fps", () => {
    expect(snapTimeToFrame(1.234, 0)).toBe(1.234);
  });
});

describe("track layout helpers", () => {
  it("stacks track heights with a 4 px gap", () => {
    const tracks = [
      { type: "media" as const },
      { type: "text" as const },
      { type: "audio" as const },
    ];
    expect(getCumulativeHeightBefore(tracks, 2)).toBe(
      TRACK_HEIGHTS.media + TRACK_HEIGHTS.text + 8,
    );
  });

  it("creates a main track when there is none and sorts audio last", () => {
    const audio: TimelineTrack = {
      id: "a",
      name: "A",
      type: "audio",
      elements: [],
    };
    const text: TimelineTrack = {
      id: "t",
      name: "T",
      type: "text",
      elements: [],
    };
    const withMain = ensureMainTrack([audio, text]);
    expect(withMain[0].isMain).toBe(true);
    expect(withMain[0].id).toMatch(/^[0-9a-f-]{36}$/);
    expect(sortTracksByOrder(withMain).map((t) => t.type)).toEqual([
      "text",
      "media",
      "audio",
    ]);
  });
});

describe("usePlaybackStore.seek", () => {
  afterEach(() => {
    usePlaybackStore.setState({
      currentTime: 0,
      duration: 0,
      isPlaying: false,
    });
  });

  it("clamps to [0, duration] and dispatches playback-seek with the clamped time", () => {
    const seen: number[] = [];
    const onSeek = (e: Event) =>
      seen.push((e as CustomEvent<{ time: number }>).detail.time);
    window.addEventListener("playback-seek", onSeek);
    try {
      usePlaybackStore.getState().setDuration(10);
      usePlaybackStore.getState().seek(12);
      expect(usePlaybackStore.getState().currentTime).toBe(10);
      usePlaybackStore.getState().seek(-3);
      expect(usePlaybackStore.getState().currentTime).toBe(0);
      usePlaybackStore.getState().seek(4.5);
      expect(usePlaybackStore.getState().currentTime).toBe(4.5);
    } finally {
      window.removeEventListener("playback-seek", onSeek);
    }
    expect(seen).toEqual([10, 0, 4.5]);
  });

  it("toggles play and pause", () => {
    const raf = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(1);
    const caf = vi
      .spyOn(window, "cancelAnimationFrame")
      .mockImplementation(() => {});
    try {
      usePlaybackStore.getState().toggle();
      expect(usePlaybackStore.getState().isPlaying).toBe(true);
      usePlaybackStore.getState().toggle();
      expect(usePlaybackStore.getState().isPlaying).toBe(false);
    } finally {
      raf.mockRestore();
      caf.mockRestore();
    }
  });
});
