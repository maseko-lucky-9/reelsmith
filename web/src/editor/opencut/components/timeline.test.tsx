/**
 * The adapted OpenCut timeline (spec 017 T148, T149; FR-008..FR-012,
 * FR-015). Mounted on the store with a fixture edit, no route and no
 * preview: element and track cases drive selection, the context menu,
 * deletion and drags; container cases the tracks, zoom, the selection box,
 * the toolbar and the ruler.
 *
 * jsdom has no layout: every rectangle is zero unless a test stubs it, so
 * drags and the selection box stub the rows' and elements' rectangles from
 * their inline geometry.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { TimelinePayload } from "@/api/client";
import { segmentsOf } from "@/lib/timelineEdit";
import { multiSegment } from "@/editor/__fixtures__/timelines";
import type { EditorTrack, TrackRole } from "@/editor/types";
import type { SplitGate } from "@/editor/renderedCut";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { TimelinePlayhead } from "./timeline-playhead";
import { TimelineTrackContent } from "./timeline-track";

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));
import { toast } from "sonner";

const store = () => useTimelineStore.getState();

/** multiSegment (3 segments, 3 captions; reel 24.95 s) plus two overlays and a voice-over. */
const EDIT: TimelinePayload = {
  ...multiSegment,
  tracks: [
    multiSegment.tracks[0],
    multiSegment.tracks[1],
    {
      kind: "text-overlay",
      items: [
        { start: 2, end: 4, text: "hook", x: 0.5, y: 0.2 },
        { start: 10, end: 12, text: "cta", x: 0.5, y: 0.8 },
      ],
    },
    { kind: "audio", items: [{ start: 1, end: 3, src: "vo.wav", gain: 1 }] },
  ],
};

const ONE_SEGMENT: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start: 100, end: 110 }] },
    { kind: "caption", items: [] },
  ],
};

const trackOf = (role: TrackRole): EditorTrack => {
  const track = store()._tracks.find((t) => t.role === role);
  if (!track) throw new Error(`no ${role} track`);
  return track;
};
const cut = () => segmentsOf(store().toTimeline()).map((s) => [s.start, s.end]);
const element = (name: string | RegExp) => screen.getByRole("button", { name });
const menuItems = () =>
  screen.getAllByRole("menuitem").map((item) => item.textContent?.trim());

function load(timeline: TimelinePayload) {
  store().clearTimeline();
  const result = store().load(timeline, {
    sourceWindow: null,
    sourceDuration: 60,
  });
  expect(result.ok).toBe(true);
}

/** Row height in px for the rectangle stubs (rows are stacked in display order). */
const ROW = 50;

/**
 * Gives every track row (`[data-track-row]`) a rectangle stacked by display
 * order and every element its inline left/width inside its row.
 */
function stubLayout() {
  return vi
    .spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockImplementation(function (this: HTMLElement) {
      const rows = Array.from(document.querySelectorAll("[data-track-row]"));
      const row = this.closest("[data-track-row]");
      const index = row ? rows.indexOf(row) : -1;
      const top = index >= 0 ? index * ROW : 0;
      const isRow = this.hasAttribute("data-track-row");
      const isElement = this.classList.contains("timeline-element");
      const left = isElement ? parseFloat(this.style.left) || 0 : 0;
      const width = isElement ? parseFloat(this.style.width) || 0 : 0;
      const height = isRow || isElement ? ROW : 0;
      return {
        x: left,
        y: top,
        left,
        top,
        width,
        height,
        right: left + width,
        bottom: top + height,
        toJSON: () => ({}),
      } as DOMRect;
    });
}

function Tracks({ splitGate }: { splitGate?: SplitGate }) {
  const tracks = useTimelineStore((s) => s.tracks);
  return (
    <div>
      {tracks.map((track) => (
        <div key={track.id} style={{ height: ROW }}>
          <TimelineTrackContent
            track={track}
            zoomLevel={1}
            splitGate={splitGate}
          />
        </div>
      ))}
    </div>
  );
}

beforeEach(() => {
  load(EDIT);
  usePlaybackStore.setState({
    currentTime: 0,
    duration: 24.95,
    isPlaying: false,
  });
  vi.mocked(toast.error).mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("timeline elements and tracks", () => {
  it("selects a caption on click and never removes it", () => {
    render(<Tracks />);
    const caption = element("Caption: one two");
    expect(caption).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(caption);

    expect(caption).toHaveAttribute("aria-pressed", "true");
    expect(store().selectedElements).toEqual([
      {
        trackId: trackOf("captions").id,
        elementId: trackOf("captions").elements[0].id,
      },
    ]);
    expect(store().toTimeline()).toStrictEqual(EDIT);
  });

  it("selects with the keyboard (Enter) and adds to the selection with Shift", () => {
    render(<Tracks />);
    fireEvent.keyDown(element("Text: hook"), { key: "Enter" });
    fireEvent.keyDown(element("Text: cta"), { key: "Enter", shiftKey: true });

    expect(store().selectedElements.map((s) => s.elementId)).toEqual(
      trackOf("overlays").elements.map((el) => el.id),
    );
  });

  it("offers Split, Split keep left, Split keep right and Delete on a segment; Split splits it at the playhead", async () => {
    usePlaybackStore.setState({ currentTime: 4 });
    render(<Tracks />);

    fireEvent.contextMenu(element(/^Segment 1/));
    await screen.findByRole("menu");
    expect(menuItems()).toEqual([
      "Split",
      "Split keep left",
      "Split keep right",
      "Delete",
    ]);

    fireEvent.click(screen.getByRole("menuitem", { name: "Split" }));

    expect(cut()).toEqual([
      [12.5, 16.5],
      [16.5, 20],
      [30, 47.25],
      [50.1, 50.3],
    ]);
  });

  it("disables a segment's split items with the gate's reason", async () => {
    usePlaybackStore.setState({ currentTime: 4 });
    render(<Tracks splitGate={{ ok: false, reason: "Re-render first." }} />);

    fireEvent.contextMenu(element(/^Segment 1/));
    const menu = await screen.findByRole("menu");

    expect(within(menu).getByText("Re-render first.")).toBeInTheDocument();
    for (const name of ["Split", "Split keep left", "Split keep right"]) {
      expect(screen.getByRole("menuitem", { name })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    }
    fireEvent.click(screen.getByRole("menuitem", { name: "Split" }));
    expect(cut()).toHaveLength(3);
  });

  it("offers no split on a caption: Delete only", async () => {
    render(<Tracks />);

    fireEvent.contextMenu(element("Caption: one two"));
    await screen.findByRole("menu");

    expect(menuItems()).toEqual(["Delete"]);
  });

  it("refuses to delete the last segment, with a toast", async () => {
    load(ONE_SEGMENT);
    render(<Tracks />);

    fireEvent.contextMenu(element(/^Segment 1/));
    await screen.findByRole("menu");
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));

    expect(toast.error).toHaveBeenCalledWith(
      expect.stringMatching(/last one cannot be deleted/),
    );
    expect(cut()).toEqual([[100, 110]]);
  });

  it("draws a voice-over as a plain bar: no canvas, no waveform", () => {
    const { container } = render(<Tracks />);
    const voiceover = element("Voice-over: vo.wav");

    expect(voiceover).toHaveTextContent("vo.wav");
    expect(voiceover.querySelector("canvas, svg, audio, wave")).toBeNull();
    expect(container.querySelector("canvas, audio, wave")).toBeNull();
  });

  it("does not drag a segment along the reel", () => {
    stubLayout();
    render(<Tracks />);
    const segment = element(/^Segment 2/);
    const box = segment.closest(".timeline-element") as HTMLElement;
    const left = box.style.left;

    fireEvent.mouseDown(segment, {
      button: 0,
      clientX: 400,
      clientY: 2 * ROW + 10,
    });
    fireEvent.mouseMove(document, { clientX: 700, clientY: 2 * ROW + 10 });

    expect(store().dragState.isDragging).toBe(false);
    expect(box.style.left).toBe(left);
    fireEvent.mouseUp(document, { clientX: 700, clientY: 2 * ROW + 10 });
    expect(store().toTimeline()).toStrictEqual(EDIT);
  });

  it("keeps an overlay dropped on a caption track on an overlay track", () => {
    stubLayout();
    render(<Tracks />);
    // Display order: captions (row 0), overlays (row 1), video (row 2), voice-over (row 3).
    const hook = element("Text: hook");
    const hookId = trackOf("overlays").elements[0].id;

    fireEvent.mouseDown(hook, { button: 0, clientX: 110, clientY: ROW + 10 });
    fireEvent.mouseMove(document, { clientX: 210, clientY: 10 });
    fireEvent.mouseUp(document, { clientX: 210, clientY: 10 });

    expect(trackOf("overlays").elements.map((el) => el.id)).toContain(hookId);
    expect(trackOf("captions").elements.map((el) => el.id)).not.toContain(
      hookId,
    );
    expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/own kind/));
    // It still moved in time on its own track: +100 px is +2 s.
    expect(trackOf("overlays").elements[0].startTime).toBe(4);
  });

  it("moves an overlay in time as one undo step", () => {
    stubLayout();
    render(<Tracks />);
    const hook = element("Text: hook");

    fireEvent.mouseDown(hook, { button: 0, clientX: 110, clientY: ROW + 10 });
    fireEvent.mouseMove(document, { clientX: 135, clientY: ROW + 10 });
    fireEvent.mouseMove(document, { clientX: 160, clientY: ROW + 10 });
    expect(store().history).toHaveLength(0);
    fireEvent.mouseUp(document, { clientX: 160, clientY: ROW + 10 });

    expect(trackOf("overlays").elements[0].startTime).toBe(3);
    expect(store().history).toHaveLength(1);
  });
  it("draws the playhead after the labels at its time, as a keyboard slider", () => {
    const onKeyDown = vi.fn();
    render(
      <TimelinePlayhead
        position={2.5}
        duration={24.95}
        zoomLevel={2}
        trackLabelsWidth={192}
        onMouseDown={() => {}}
        onKeyDown={onKeyDown}
      />,
    );
    const playhead = screen.getByRole("slider", { name: "Playhead" });

    // 192 px of labels + 2.5 s x 50 px/s x zoom 2.
    expect(playhead.style.left).toBe("442px");
    expect(playhead).toHaveAttribute("aria-valuenow", "2.5");
    expect(playhead).toHaveAttribute("aria-valuemax", "24.95");
    fireEvent.keyDown(playhead, { key: "ArrowRight" });
    expect(onKeyDown).toHaveBeenCalledTimes(1);
  });
});
