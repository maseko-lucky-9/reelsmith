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
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { TimelinePayload } from "@/api/client";
import { segmentsOf } from "@/lib/timelineEdit";
import { multiSegment } from "@/editor/__fixtures__/timelines";
import type { EditorTrack, TrackRole } from "@/editor/types";
import type { SplitGate } from "@/editor/renderedCut";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { Timeline } from "./timeline";
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

describe("the playhead on a scrolled timeline (PR #97 review, blocker C)", () => {
  /** A 60 s reel: 3000 px of content at zoom 1. */
  const SIXTY: TimelinePayload = {
    schema_version: 2,
    tracks: [
      {
        kind: "video",
        items: [
          { start: 0, end: 30 },
          { start: 40, end: 70 },
        ],
      },
    ],
  };
  const playhead = () => screen.getByRole("slider", { name: "Playhead" });

  it("draws the line at its time minus the scroll offset, after the labels", () => {
    render(
      <TimelinePlayhead
        position={30}
        duration={60}
        zoomLevel={1}
        trackLabelsWidth={192}
        scrollLeft={1096}
        viewportWidth={808}
        onMouseDown={() => {}}
        onKeyDown={() => {}}
      />,
    );
    // 192 px of labels + 30 s x 50 px/s - 1096 px scrolled.
    expect(playhead().style.left).toBe("596px");
  });

  it("hides the line while it is scrolled out of the tracks' view", () => {
    const { rerender } = render(
      <TimelinePlayhead
        position={30}
        duration={60}
        zoomLevel={1}
        trackLabelsWidth={192}
        scrollLeft={0}
        viewportWidth={808}
        onMouseDown={() => {}}
        onKeyDown={() => {}}
      />,
    );
    // 1500 px into the content, the view shows 0-808 px: past its right edge.
    expect(screen.queryByRole("slider", { name: "Playhead" })).toBeNull();
    rerender(
      <TimelinePlayhead
        position={2}
        duration={60}
        zoomLevel={1}
        trackLabelsWidth={192}
        scrollLeft={500}
        viewportWidth={808}
        onMouseDown={() => {}}
        onKeyDown={() => {}}
      />,
    );
    // 100 px into the content, the view starts at 500 px: left of it, not over the labels.
    expect(screen.queryByRole("slider", { name: "Playhead" })).toBeNull();
  });

  it("follows the tracks' scroll: a seek to 30 s on a 1000 px timeline draws it on screen (596 px, not 1692 px)", () => {
    load(SIXTY);
    const { container } = render(<Timeline />);
    const viewports = Array.from(
      container.querySelectorAll<HTMLElement>(
        '[data-slot="scroll-area-viewport"]',
      ),
    );
    const tracks = container.querySelector(
      '[data-tracks-area] [data-slot="scroll-area-viewport"]',
    ) as HTMLElement;
    // jsdom has no layout: the ruler and the tracks show 808 px of 3000 px.
    for (const viewport of viewports) {
      Object.defineProperty(viewport, "clientWidth", {
        configurable: true,
        value: 808,
      });
      Object.defineProperty(viewport, "scrollWidth", {
        configurable: true,
        value: 3000,
      });
    }

    act(() => usePlaybackStore.getState().seek(30));
    // The auto-scroll centred the playhead (1500 - 808 / 2); the browser then fires scroll.
    expect(tracks.scrollLeft).toBe(1096);
    act(() => {
      tracks.dispatchEvent(new Event("scroll"));
    });

    expect(playhead().style.left).toBe("596px");
  });
});

describe("Timeline", () => {
  const button = (name: string) =>
    screen.getByRole("button", { name }) as HTMLButtonElement;
  const content = (container: HTMLElement) =>
    container.querySelector("[data-timeline-content]") as HTMLElement;

  it("renders the four tracks of the edit with their labels", () => {
    const { container } = render(<Timeline />);

    const rows = Array.from(
      container.querySelectorAll<HTMLElement>("[data-track-row]"),
    );
    expect(rows.map((row) => row.dataset.trackRole)).toEqual([
      "captions",
      "overlays",
      "segments",
      "voiceovers",
    ]);
    const labels = container.querySelector(
      "[data-track-labels]",
    ) as HTMLElement;
    for (const name of ["Captions", "Text", "Video", "Voice-over"]) {
      expect(within(labels).getByText(name)).toBeInTheDocument();
    }
    expect(screen.getAllByRole("button", { name: /^Segment / })).toHaveLength(
      3,
    );
    expect(screen.getAllByRole("button", { name: /^Caption: / })).toHaveLength(
      3,
    );
    expect(screen.getAllByRole("button", { name: /^Text: / })).toHaveLength(2);
    expect(
      screen.getAllByRole("button", { name: /^Voice-over: / }),
    ).toHaveLength(1);
  });

  it("runs the playhead on the reel's length and shows it", () => {
    render(<Timeline />);

    expect(usePlaybackStore.getState().duration).toBeCloseTo(24.95, 9);
    expect(screen.getByRole("slider", { name: "Playhead" })).toHaveAttribute(
      "aria-valuemax",
      "24.95",
    );
  });

  it("runs the playhead on another clock's length when given one (the rendered clip), and back on the reel's", () => {
    usePlaybackStore.setState({ currentTime: 28 });
    const { rerender } = render(<Timeline playbackDuration={30} />);
    expect(usePlaybackStore.getState().duration).toBe(30);
    expect(usePlaybackStore.getState().currentTime).toBe(28);

    rerender(<Timeline />);
    expect(usePlaybackStore.getState().duration).toBeCloseTo(24.95, 9);
    expect(usePlaybackStore.getState().currentTime).toBeCloseTo(24.95, 9);
  });

  it("doubles the content width on Zoom in and halves it on Zoom out", () => {
    const { container } = render(<Timeline />);
    const width = () => parseFloat(content(container).style.width);
    // 24.95 s at 50 px/s.
    expect(width()).toBeCloseTo(1247.5, 6);

    fireEvent.click(button("Zoom in"));
    expect(width()).toBeCloseTo(2495, 6);

    fireEvent.click(button("Zoom out"));
    fireEvent.click(button("Zoom out"));
    expect(width()).toBeCloseTo(623.75, 6);
  });

  it("selects the overlays a drag-out selection box crosses", () => {
    stubLayout();
    const { container } = render(<Timeline />);
    const area = container.querySelector("[data-tracks-area]") as HTMLElement;
    // Rows: captions 0-50, overlays 50-100, video 100-150. Overlays at 100-200 and 500-600 px.
    fireEvent.mouseDown(area, { button: 0, clientX: 50, clientY: ROW + 5 });
    fireEvent.mouseMove(window, { clientX: 650, clientY: 2 * ROW - 5 });
    fireEvent.mouseUp(window, { clientX: 650, clientY: 2 * ROW - 5 });

    const overlays = trackOf("overlays");
    expect(store().selectedElements).toEqual(
      overlays.elements.map((el) => ({
        trackId: overlays.id,
        elementId: el.id,
      })),
    );
  });

  it("disables Split, keep left and keep right with the gate's reason", () => {
    usePlaybackStore.setState({ currentTime: 4 });
    render(<Timeline splitGate={{ ok: false, reason: "Re-render first." }} />);

    for (const name of ["Split", "Split keep left", "Split keep right"]) {
      expect(button(name)).toBeDisabled();
      expect(button(name)).toHaveAccessibleDescription("Re-render first.");
      expect(button(name).title).toBe("Re-render first.");
    }
    fireEvent.keyDown(document.body, { key: "s" });
    expect(toast.error).toHaveBeenCalledWith("Re-render first.");
    expect(cut()).toHaveLength(3);
  });

  it("splits the segment under the playhead from the toolbar when the gate is open", () => {
    usePlaybackStore.setState({ currentTime: 10 });
    render(<Timeline />);

    expect(button("Split")).toBeEnabled();
    expect(button("Split").title).toBe("");
    fireEvent.click(button("Split"));

    // Reel 10 s is 2.5 s into segment 2 (reel 7.5-24.75, source 30-47.25).
    expect(cut()).toEqual([
      [12.5, 20],
      [30, 32.5],
      [32.5, 47.25],
      [50.1, 50.3],
    ]);
  });

  it("enables Undo and Redo as the store's history allows", () => {
    usePlaybackStore.setState({ currentTime: 4 });
    render(<Timeline />);
    expect(button("Undo")).toBeDisabled();
    expect(button("Redo")).toBeDisabled();

    fireEvent.click(button("Split keep left"));
    expect(cut()[0]).toEqual([12.5, 16.5]);
    expect(button("Undo")).toBeEnabled();

    fireEvent.click(button("Undo"));
    expect(cut()[0]).toEqual([12.5, 20]);
    expect(button("Undo")).toBeDisabled();
    expect(button("Redo")).toBeEnabled();

    fireEvent.click(button("Redo"));
    expect(cut()[0]).toEqual([12.5, 16.5]);
  });

  it("deletes the selection from the toolbar", () => {
    render(<Timeline />);
    expect(button("Delete")).toBeDisabled();

    fireEvent.click(element("Text: cta"));
    fireEvent.click(button("Delete"));

    expect(
      trackOf("overlays").elements.map((el) =>
        el.type === "text" ? el.content : null,
      ),
    ).toEqual(["hook"]);
  });

  it("moves the playhead to a ruler click, snapped to a frame", () => {
    const { container } = render(<Timeline />);
    const ruler = container.querySelector("[data-ruler]") as HTMLElement;

    fireEvent.mouseDown(ruler, { button: 0, clientX: 101 });
    fireEvent.mouseUp(window, { clientX: 101 });

    expect(usePlaybackStore.getState().currentTime).toBeCloseTo(61 / 30, 9);
    expect(screen.getByRole("slider", { name: "Playhead" })).toHaveAttribute(
      "aria-valuenow",
      "2.033",
    );
  });

  it("toggles the source's mute from the Video track label", () => {
    render(<Timeline />);
    const mute = button("Mute source audio");
    expect(mute).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(mute);

    expect(mute).toHaveAttribute("aria-pressed", "true");
    expect(store().toTimeline().mute_source).toBe(true);
  });
});
