/**
 * The adapted OpenCut timeline hooks (spec 017 T147; FR-009..FR-011,
 * research R12): the ruler and playhead snap to 1/30 s; a segment's edge
 * drag stops at its neighbour's source window and commits once; the key map
 * (Space, S, Delete/Backspace, Cmd/Ctrl+Z, Shift+Cmd/Ctrl+Z, Cmd/Ctrl+Y)
 * is ignored while typing, and split waits on the split gate.
 */
import type React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, renderHook } from "@testing-library/react";
import type { TimelinePayload } from "@/api/client";
import { segmentsOf } from "@/lib/timelineEdit";
import type { EditorTrack, TrackRole } from "@/editor/types";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { usePlaybackControls } from "./use-playback-controls";
import { useTimelineElementResize } from "./use-timeline-element-resize";
import { TIMELINE_FPS, useTimelinePlayhead } from "./use-timeline-playhead";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
import { toast } from "sonner";

const store = () => useTimelineStore.getState();

/** Two segments 10 s apart in the source, a caption and a text overlay. */
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
    { kind: "caption", items: [{ start: 5, end: 6, text: "hi", words: [] }] },
    {
      kind: "text-overlay",
      items: [{ start: 2, end: 4, text: "hook", x: 0.5, y: 0.2 }],
    },
  ],
};

const trackOf = (role: TrackRole): EditorTrack => {
  const track = store()._tracks.find((t) => t.role === role);
  if (!track) throw new Error(`no ${role} track`);
  return track;
};
const cut = () => segmentsOf(store().toTimeline()).map((s) => [s.start, s.end]);
const mouse = (clientX: number, target?: Element) =>
  ({
    button: 0,
    clientX,
    target,
    preventDefault: () => {},
    stopPropagation: () => {},
  }) as unknown as React.MouseEvent;

beforeEach(() => {
  store().clearTimeline();
  const result = store().load(TWO, { sourceWindow: null, sourceDuration: 200 });
  expect(result.ok).toBe(true);
  usePlaybackStore.setState({ currentTime: 0, duration: 20, isPlaying: false });
  vi.mocked(toast.error).mockClear();
});

afterEach(() => {
  usePlaybackStore.getState().pause();
  document.body.replaceChildren();
});

describe("useTimelinePlayhead", () => {
  function renderPlayhead(seek = vi.fn(), currentTime = 0) {
    const ruler = document.createElement("div");
    document.body.append(ruler);
    const hook = renderHook(() =>
      useTimelinePlayhead({
        currentTime,
        duration: 20,
        zoomLevel: 1,
        seek,
        rulerRef: { current: ruler },
        rulerScrollRef: { current: null },
        tracksScrollRef: { current: null },
      }),
    );
    return { ...hook, ruler, seek };
  }

  it("snaps a ruler click to 1/30 s and clamps it to the reel", () => {
    const { result, ruler, seek } = renderPlayhead();
    expect(TIMELINE_FPS).toBe(30);

    // 101 px at 50 px/s is 2.02 s: frame 61 at 30 fps is 2.0333 s.
    act(() => result.current.handleRulerMouseDown(mouse(101, ruler)));
    expect(seek).toHaveBeenLastCalledWith(61 / 30);
    act(() => {
      fireEvent.mouseUp(window, { clientX: 101 });
    });
    expect(seek).toHaveBeenLastCalledWith(61 / 30);

    act(() => result.current.handleRulerMouseDown(mouse(5000, ruler)));
    expect(seek).toHaveBeenLastCalledWith(20);
  });

  it("moves the playhead one frame per arrow key, one second with Shift", () => {
    const seek = vi.fn();
    const { result } = renderPlayhead(seek, 3);
    const key = (k: string, shiftKey = false) =>
      ({ key: k, shiftKey, preventDefault: () => {} }) as React.KeyboardEvent;

    act(() => result.current.handlePlayheadKeyDown(key("ArrowRight")));
    expect(seek).toHaveBeenLastCalledWith(91 / 30);
    act(() => result.current.handlePlayheadKeyDown(key("ArrowLeft", true)));
    expect(seek).toHaveBeenLastCalledWith(2);
    act(() => result.current.handlePlayheadKeyDown(key("End")));
    expect(seek).toHaveBeenLastCalledWith(20);
  });
});

describe("useTimelineElementResize", () => {
  it("stops a segment's right edge at the next segment's source start and commits once", () => {
    const track = trackOf("segments");
    const element = track.elements[0];
    const { result } = renderHook(() =>
      useTimelineElementResize({ element, track, zoomLevel: 1 }),
    );
    expect(result.current.handles).toEqual({ left: true, right: true });

    act(() => result.current.handleResizeStart(mouse(0), element.id, "right"));
    // +1000 px is +20 s: 110 + 20 would run 10 s into the next segment (120).
    act(() => {
      fireEvent.mouseMove(document, { clientX: 1000 });
    });
    expect(result.current.preview).toEqual({
      startTime: 0,
      visibleDuration: 20,
    });
    expect(store().history).toHaveLength(0);

    act(() => {
      fireEvent.mouseUp(document, { clientX: 1000 });
    });
    expect(cut()).toEqual([
      [100, 120],
      [120, 130],
    ]);
    expect(store().history).toHaveLength(1);
    expect(result.current.preview).toBeNull();
  });

  it("refuses an overlay edge drag onto another overlay of its track, like a move, with a toast", () => {
    store().clearTimeline();
    store().load(
      {
        ...TWO,
        tracks: [
          TWO.tracks[0],
          {
            kind: "text-overlay",
            items: [
              { start: 2, end: 4, text: "hook", x: 0.5, y: 0.2 },
              { start: 5, end: 7, text: "cta", x: 0.5, y: 0.8 },
            ],
          },
        ],
      },
      { sourceWindow: null, sourceDuration: 200 },
    );
    const track = trackOf("overlays");
    const element = track.elements[0];
    const before = store().toTimeline();
    const { result } = renderHook(() =>
      useTimelineElementResize({ element, track, zoomLevel: 1 }),
    );

    // The right edge +100 px (+2 s): 2-6 s runs into cta at 5-7 s.
    act(() => result.current.handleResizeStart(mouse(0), element.id, "right"));
    act(() => {
      fireEvent.mouseUp(document, { clientX: 100 });
    });

    expect(toast.error).toHaveBeenCalledWith(
      expect.stringMatching(/overlap another item/),
    );
    expect(store().toTimeline()).toStrictEqual(before);
    expect(store().history).toHaveLength(0);

    // +25 px (+0.5 s), 2-4.5 s, is clear of it.
    act(() => result.current.handleResizeStart(mouse(0), element.id, "right"));
    act(() => {
      fireEvent.mouseUp(document, { clientX: 25 });
    });
    expect(trackOf("overlays").elements[0].duration).toBe(2.5);
  });

  it("draws no handles for captions", () => {
    const track = trackOf("captions");
    const { result } = renderHook(() =>
      useTimelineElementResize({
        element: track.elements[0],
        track,
        zoomLevel: 1,
      }),
    );
    expect(result.current.handles).toEqual({ left: false, right: false });
  });
});

describe("usePlaybackControls", () => {
  const key = (init: KeyboardEventInit, target: Element = document.body) =>
    fireEvent.keyDown(target, init);

  it("S splits the segment under the playhead when the gate is open", () => {
    usePlaybackStore.setState({ currentTime: 4 });
    renderHook(() => usePlaybackControls());

    key({ key: "s" });

    expect(cut()).toEqual([
      [100, 104],
      [104, 110],
      [120, 130],
    ]);
  });

  it("S toasts the gate's reason and changes nothing when the gate is closed", () => {
    usePlaybackStore.setState({ currentTime: 4 });
    renderHook(() =>
      usePlaybackControls({
        splitGate: { ok: false, reason: "Re-render first." },
      }),
    );

    key({ key: "s" });

    expect(toast.error).toHaveBeenCalledWith("Re-render first.");
    expect(cut()).toEqual([
      [100, 110],
      [120, 130],
    ]);
    expect(store().history).toHaveLength(0);
  });

  it("S toasts why when the playhead is too near a segment's edge", () => {
    usePlaybackStore.setState({ currentTime: 0.02 });
    renderHook(() => usePlaybackControls());

    key({ key: "s" });

    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/0\.05 s/));
    expect(cut()).toHaveLength(2);
  });

  it("does nothing for keys typed in a textarea, an input or contenteditable", () => {
    usePlaybackStore.setState({ currentTime: 4 });
    store().splitElement(
      trackOf("segments").id,
      trackOf("segments").elements[1].id,
      15,
    );
    const overlay = trackOf("overlays");
    store().selectElement(overlay.id, overlay.elements[0].id);
    renderHook(() => usePlaybackControls());
    const before = store().toTimeline();
    const textarea = document.createElement("textarea");
    const input = document.createElement("input");
    const editable = document.createElement("div");
    editable.setAttribute("contenteditable", "true");
    document.body.append(textarea, input, editable);

    for (const target of [textarea, input, editable]) {
      key({ key: "s" }, target);
      key({ key: "Delete" }, target);
      key({ key: "Backspace" }, target);
      key({ key: "z", metaKey: true }, target);
      key({ key: " " }, target);
    }

    expect(store().toTimeline()).toStrictEqual(before);
    expect(store().history).toHaveLength(1);
    expect(usePlaybackStore.getState().isPlaying).toBe(false);
  });

  it("undoes with Cmd/Ctrl+Z and redoes with Shift+Cmd/Ctrl+Z and Cmd/Ctrl+Y", () => {
    usePlaybackStore.setState({ currentTime: 4 });
    renderHook(() => usePlaybackControls());
    key({ key: "s" });
    expect(cut()).toHaveLength(3);

    key({ key: "z", metaKey: true });
    expect(cut()).toHaveLength(2);
    key({ key: "z", metaKey: true, shiftKey: true });
    expect(cut()).toHaveLength(3);
    key({ key: "z", ctrlKey: true });
    expect(cut()).toHaveLength(2);
    key({ key: "y", ctrlKey: true });
    expect(cut()).toHaveLength(3);
  });

  it("leaves Cmd/Ctrl+S to the browser: it no longer splits", () => {
    usePlaybackStore.setState({ currentTime: 4 });
    renderHook(() => usePlaybackControls());

    const notCancelledMeta = key({ key: "s", metaKey: true });
    const notCancelledCtrl = key({ key: "s", ctrlKey: true });

    expect(cut()).toHaveLength(2);
    expect(notCancelledMeta).toBe(true);
    expect(notCancelledCtrl).toBe(true);
  });

  it("Delete removes the whole selection as one undo step", () => {
    const captions = trackOf("captions");
    const overlays = trackOf("overlays");
    store().selectElement(captions.id, captions.elements[0].id);
    store().selectElement(overlays.id, overlays.elements[0].id, true);
    renderHook(() => usePlaybackControls());

    key({ key: "Delete" });

    expect(trackOf("captions").elements).toHaveLength(0);
    expect(trackOf("overlays").elements).toHaveLength(0);
    expect(store().selectedElements).toEqual([]);
    store().undo();
    expect(store().toTimeline()).toStrictEqual(TWO);
  });

  it("Backspace on the last segment is refused with a toast", () => {
    store().deleteSegment(trackOf("segments").elements[1].id);
    const segments = trackOf("segments");
    store().selectElement(segments.id, segments.elements[0].id);
    renderHook(() => usePlaybackControls());

    key({ key: "Backspace" });

    expect(toast.error).toHaveBeenCalledWith(
      expect.stringMatching(/last one cannot be deleted/),
    );
    expect(cut()).toEqual([[100, 110]]);
  });

  it("Space plays and pauses, but not while a button has focus", () => {
    renderHook(() => usePlaybackControls());
    const button = document.createElement("button");
    document.body.append(button);

    key({ key: " " }, button);
    expect(usePlaybackStore.getState().isPlaying).toBe(false);

    key({ key: " " });
    expect(usePlaybackStore.getState().isPlaying).toBe(true);
    key({ key: " " });
    expect(usePlaybackStore.getState().isPlaying).toBe(false);
  });
});
