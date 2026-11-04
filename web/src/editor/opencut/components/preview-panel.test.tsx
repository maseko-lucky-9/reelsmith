/**
 * The adapted OpenCut preview panel (spec 017 T153; FR-013..FR-016): the
 * job's clean source played through the edit's segments on the playback
 * store's reel clock, captions and text drawn on top in the render's font and
 * looks, and the rendered clip as the result view.
 */
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TimelinePayload } from "@/api/client";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { PreviewPanel, type PreviewPanelProps } from "./preview-panel";

const WHITE = "rgb(255, 255, 255)";
const HORMOZI_HIGHLIGHT = "rgb(255, 242, 0)"; // #fff200

/** Segments [10, 14] and [20, 25] (a 9 s reel), one worded caption, one overlay. */
function edit(overrides: Partial<TimelinePayload> = {}): TimelinePayload {
  return {
    schema_version: 2,
    caption_style: "hormozi",
    tracks: [
      {
        kind: "video",
        items: [
          { start: 10, end: 14 },
          { start: 20, end: 25 },
        ],
      },
      {
        kind: "caption",
        items: [
          {
            start: 1,
            end: 3,
            text: "hello big world",
            words: [
              { word: "hello", start: 1, end: 1.5 },
              { word: "big", start: 1.5, end: 2 },
              { word: "world", start: 2, end: 3 },
            ],
          },
        ],
      },
      {
        kind: "text-overlay",
        items: [
          {
            start: 0,
            end: 9,
            text: "Hook",
            x: 0.1,
            y: 0.3,
            font_size: 48,
            color: "#ff0000",
          },
        ],
      },
    ],
    ...overrides,
  };
}

const timeline = () => useTimelineStore.getState();
const playback = () => usePlaybackStore.getState();

function load(t: TimelinePayload) {
  expect(
    timeline().load(t, { sourceWindow: null, sourceDuration: 60 }).ok,
  ).toBe(true);
}

function mount(props: Partial<PreviewPanelProps> = {}) {
  const onUnavailable = vi.fn();
  const utils = render(
    <PreviewPanel
      view="source"
      jobId="job-1"
      clipId="clip-1"
      videoVersion={0}
      onUnavailable={onUnavailable}
      {...props}
    />,
  );
  const video = utils.container.querySelector("video");
  if (!video) throw new Error("no <video>");
  return { ...utils, video, onUnavailable };
}

/** jsdom has no media pipeline: give the element what a loaded source reports. */
function loadMetadata(
  video: HTMLVideoElement,
  width: number,
  height: number,
  duration: number,
) {
  Object.defineProperty(video, "videoWidth", {
    configurable: true,
    value: width,
  });
  Object.defineProperty(video, "videoHeight", {
    configurable: true,
    value: height,
  });
  Object.defineProperty(video, "duration", {
    configurable: true,
    value: duration,
  });
  fireEvent.loadedMetadata(video);
}

let play: ReturnType<typeof vi.spyOn>;
let pause: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  timeline().clearTimeline();
  usePlaybackStore.setState({
    isPlaying: false,
    currentTime: 0,
    duration: 9,
    volume: 1,
    muted: false,
    speed: 1,
  });
  play = vi
    .spyOn(HTMLMediaElement.prototype, "play")
    .mockResolvedValue(undefined);
  pause = vi
    .spyOn(HTMLMediaElement.prototype, "pause")
    .mockImplementation(() => {});
  load(edit());
});

afterEach(() => {
  cleanup();
  usePlaybackStore.setState({ isPlaying: false });
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("source view", () => {
  it("plays the job's source, fetching only its metadata up front", () => {
    const { video } = mount();
    expect(video.getAttribute("src")).toBe("/api/jobs/job-1/source");
    expect(video.getAttribute("preload")).toBe("metadata");
  });

  it("opens on the first segment's start", () => {
    const { video } = mount();
    expect(video.currentTime).toBe(10);
  });

  it("seeking the store to reel 5 s shows source 21 s", () => {
    const { video } = mount();
    act(() => playback().seek(5));
    expect(video.currentTime).toBe(21);
  });

  it("continues at the next segment's start when playback reaches a segment's end", () => {
    const { video } = mount();
    video.currentTime = 14;
    fireEvent.timeUpdate(video);
    expect(video.currentTime).toBe(20);
  });

  it("plays straight through where a split's two halves meet, with no seek", () => {
    load(
      edit({
        tracks: [
          {
            kind: "video",
            items: [
              { start: 10, end: 12 },
              { start: 12, end: 14 },
            ],
          },
        ],
      }),
    );
    const { video } = mount();
    act(() => playback().seek(1.99));
    // Record every seek from here on: a seek to where the video already is still stalls it.
    let current = 12;
    const seeks: number[] = [];
    Object.defineProperty(video, "currentTime", {
      configurable: true,
      get: () => current,
      set: (value: number) => {
        seeks.push(value);
        current = value;
      },
    });
    fireEvent.timeUpdate(video);
    act(() => {
      window.dispatchEvent(
        new CustomEvent("playback-update", { detail: { time: 2 } }),
      );
    });
    expect(seeks).toEqual([]);
  });

  it("leaves playback alone inside a segment", () => {
    const { video } = mount();
    video.currentTime = 13.5;
    fireEvent.timeUpdate(video);
    expect(video.currentTime).toBe(13.5);
  });

  it("corrects drift over 0.5 s on the reel clock and seeks out of a cut part", () => {
    const { video } = mount();
    const update = (time: number) =>
      act(() => {
        window.dispatchEvent(
          new CustomEvent("playback-update", { detail: { time } }),
        );
      });
    video.currentTime = 21.8; // reel 5.8: 0.2 s off reel 6
    update(6);
    expect(video.currentTime).toBe(21.8);
    video.currentTime = 20.5; // reel 4.5: 1.5 s off
    update(6);
    expect(video.currentTime).toBe(22);
    video.currentTime = 16; // between the segments: on no reel time at all
    update(6);
    expect(video.currentTime).toBe(22);
    video.currentTime = 14.01; // ran just past segment 1's end, ahead of the playhead (reel 3.995)
    update(3.995);
    expect(video.currentTime).toBe(20); // forward to segment 2, not back into segment 1
  });

  it("shows a trim at once: the frame under the playhead follows the new segments", () => {
    const { video } = mount();
    act(() => playback().seek(1));
    expect(video.currentTime).toBe(11);
    const first = timeline()._tracks.find((t) => t.role === "segments")!
      .elements[0];
    act(() => {
      expect(timeline().trimSegment(first.id, { start: 12 }).ok).toBe(true);
    });
    expect(video.currentTime).toBe(13);
  });

  it("plays and pauses with the store, and pauses past the reel", () => {
    mount();
    play.mockClear();
    act(() => usePlaybackStore.setState({ isPlaying: true }));
    expect(play).toHaveBeenCalledTimes(1);
    pause.mockClear();
    act(() => usePlaybackStore.setState({ isPlaying: false }));
    expect(pause).toHaveBeenCalled();
    play.mockClear();
    act(() => usePlaybackStore.setState({ currentTime: 9.5, isPlaying: true }));
    expect(play).not.toHaveBeenCalled();
  });

  it("is muted when the edit mutes the source", () => {
    const { video } = mount();
    expect(video.muted).toBe(false);
    const segments = timeline()._tracks.find((t) => t.role === "segments")!;
    act(() => {
      expect(timeline().toggleTrackMute(segments.id).ok).toBe(true);
    });
    expect(timeline().toTimeline().mute_source).toBe(true);
    expect(video.muted).toBe(true);
  });

  it("reports the source's length to the timeline store when its metadata loads", () => {
    const { video } = mount();
    expect(timeline().sourceDuration).toBe(60);
    loadMetadata(video, 1920, 1080, 120);
    expect(timeline().sourceDuration).toBe(120);
  });

  it("calls onUnavailable with a reason when the source fails to load", () => {
    const { video, onUnavailable } = mount();
    Object.defineProperty(video, "error", {
      configurable: true,
      value: { code: 4 },
    });
    fireEvent.error(video);
    expect(onUnavailable).toHaveBeenCalledTimes(1);
    expect(onUnavailable.mock.calls[0][0]).toMatch(
      /source video is not available/i,
    );
  });
});

describe("caption layer", () => {
  it("draws the active caption in the render's font with the hormozi fill and its active word highlighted", () => {
    mount();
    act(() => playback().seek(1.6));
    const caption = screen.getByTestId("preview-caption");
    expect(caption.style.fontFamily).toContain("Reelsmith Caption");
    expect(within(caption).getByText("hello").style.color).toBe(WHITE);
    expect(within(caption).getByText("big").style.color).toBe(
      HORMOZI_HIGHLIGHT,
    );
    expect(within(caption).getByText("world").style.color).toBe(WHITE);
  });

  it("highlights nothing in the subtle look", () => {
    load(edit({ caption_style: "subtle" }));
    mount();
    act(() => playback().seek(1.6));
    const caption = screen.getByTestId("preview-caption");
    for (const word of ["hello", "big", "world"]) {
      expect(within(caption).getByText(word).style.color).toBe(WHITE);
    }
  });

  it("shows no caption outside its time", () => {
    mount();
    act(() => playback().seek(4));
    expect(screen.queryByTestId("preview-caption")).toBeNull();
  });

  it("anchors the caption in the band below a 16:9 source, at 85% for a 9:16 one", () => {
    const { video } = mount();
    act(() => playback().seek(1.6));
    expect(screen.getByTestId("preview-caption").style.top).toBe("82.9167%"); // 1592 / 1920
    loadMetadata(video, 1080, 1920, 120);
    expect(screen.getByTestId("preview-caption").style.top).toBe("85%"); // 1632 / 1920
  });

  it("scales the look's font size by preview width / canvas width", () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        private readonly callback: ResizeObserverCallback;
        constructor(callback: ResizeObserverCallback) {
          this.callback = callback;
        }
        observe() {
          this.callback([], this as unknown as ResizeObserver);
        }
        unobserve() {}
        disconnect() {}
      },
    );
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 270,
      height: 480,
      top: 0,
      left: 0,
      right: 270,
      bottom: 480,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    mount();
    act(() => playback().seek(1.6));
    // 270 px wide preview of a 1080 px canvas: hormozi's 96 px is 24 px, the overlay's 48 px is 12 px.
    expect(screen.getByTestId("preview-caption").style.fontSize).toBe("24px");
    expect(screen.getByTestId("preview-overlay").style.fontSize).toBe("12px");
  });
});

describe("overlay layer", () => {
  it("places an overlay at its x/y fractions of the frame, in its colour", () => {
    mount();
    const overlay = screen.getByTestId("preview-overlay");
    expect(overlay).toHaveTextContent("Hook");
    expect(overlay.style.left).toBe("10%");
    expect(overlay.style.top).toBe("30%");
    expect(overlay.style.color).toBe("rgb(255, 0, 0)");
    expect(overlay.style.fontFamily).toContain("Reelsmith Caption");
  });

  it("renders caption and overlay markup literally, never as HTML", () => {
    load(
      edit({
        tracks: [
          { kind: "video", items: [{ start: 10, end: 14 }] },
          {
            kind: "caption",
            items: [{ start: 0, end: 2, text: "<b>x</b>", words: [] }],
          },
          {
            kind: "text-overlay",
            items: [{ start: 0, end: 2, text: "<i>y</i>", x: 0.5, y: 0.5 }],
          },
        ],
      }),
    );
    const { container } = mount();
    act(() => playback().seek(1));
    expect(screen.getByText("<b>x</b>")).toBeInTheDocument();
    expect(screen.getByText("<i>y</i>")).toBeInTheDocument();
    expect(container.querySelector("b, i")).toBeNull();
  });
});

describe("result view", () => {
  it("plays the rendered clip, cache-busted once re-rendered", () => {
    const { video } = mount({ view: "result", videoVersion: 2 });
    expect(video.getAttribute("src")).toBe("/api/clips/clip-1/video?v=2");
  });

  it("asks for the plain clip URL before any re-render", () => {
    const { video } = mount({ view: "result", videoVersion: 0 });
    expect(video.getAttribute("src")).toBe("/api/clips/clip-1/video");
  });

  it("runs the playhead on the rendered clip's own clock, with no caption drawn over it", () => {
    const { video } = mount({ view: "result" });
    act(() => playback().seek(5));
    expect(video.currentTime).toBe(5);
    video.currentTime = 3.5;
    fireEvent.timeUpdate(video);
    expect(playback().currentTime).toBe(3.5);
    act(() => playback().seek(1.6));
    expect(screen.queryByTestId("preview-caption")).toBeNull();
  });

  it("reports the rendered clip's length when its metadata loads", () => {
    const onResultDuration = vi.fn();
    const { video } = mount({ view: "result", onResultDuration });
    loadMetadata(video, 1080, 1920, 12.5);
    expect(onResultDuration).toHaveBeenLastCalledWith(12.5);
    expect(
      screen.getByText("00:00:12:15", { exact: false }),
    ).toBeInTheDocument();
  });
});

describe("toolbar", () => {
  it("toggles playback", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(playback().isPlaying).toBe(true);
    act(() => playback().pause());
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });
});
