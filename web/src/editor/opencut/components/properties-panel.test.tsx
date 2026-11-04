/**
 * The adapted OpenCut properties panel (spec 017 T150; FR-009, research R9):
 * schema v2 fields only. A caption shows its text (and a note when its word
 * count no longer matches its timed words); a text overlay its text, font
 * size, colour and x/y; no font picker, rotation, opacity, background,
 * alignment, weight or decoration. Edits reach `toTimeline()` with only the
 * edited fields changed, each as one undo step.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { TimelineItem, TimelinePayload } from "@/api/client";
import type { EditorTrack, TrackRole } from "@/editor/types";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { PropertiesPanel } from "./properties-panel";

const store = () => useTimelineStore.getState();

const HOOK: TimelineItem = {
  start: 2,
  end: 4,
  text: "hook",
  x: 0.1,
  y: 0.3,
  font_size: 48,
  color: "#ffcc00",
  note: "kept",
};

const EDIT: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: "video", items: [{ start: 100, end: 110, src: "main" }] },
    {
      kind: "caption",
      items: [
        {
          start: 0.5,
          end: 1.7,
          text: "matching words",
          words: [
            { word: "matching", start: 0.5, end: 1.1 },
            { word: "words", start: 1.1, end: 1.7 },
          ],
        },
        {
          start: 2,
          end: 3.3,
          text: "now three words",
          words: [
            { word: "two", start: 2, end: 2.6 },
            { word: "words", start: 2.6, end: 3.3 },
          ],
        },
      ],
    },
    { kind: "text-overlay", items: [HOOK] },
    { kind: "audio", items: [{ start: 1, end: 3, src: "vo.wav", gain: 1 }] },
  ],
};

const trackOf = (role: TrackRole): EditorTrack => {
  const track = store()._tracks.find((t) => t.role === role);
  if (!track) throw new Error(`no ${role} track`);
  return track;
};
const select = (role: TrackRole, i = 0) =>
  store().selectElement(trackOf(role).id, trackOf(role).elements[i].id);
const overlayItem = () =>
  store()
    .toTimeline()
    .tracks.find((t) => t.kind === "text-overlay")!.items[0];
const captionItems = () =>
  store()
    .toTimeline()
    .tracks.find((t) => t.kind === "caption")!.items;

function commit(field: HTMLElement, value: string) {
  fireEvent.change(field, { target: { value } });
  fireEvent.blur(field);
}

beforeEach(() => {
  store().clearTimeline();
  const result = store().load(EDIT, {
    sourceWindow: null,
    sourceDuration: 200,
  });
  expect(result.ok).toBe(true);
});

describe("PropertiesPanel", () => {
  it("edits an overlay's text and font size, changing only those two fields", () => {
    select("overlays");
    render(<PropertiesPanel />);

    commit(screen.getByRole("textbox", { name: "Text" }), "new hook");
    commit(screen.getByRole("spinbutton", { name: "Font size" }), "72");

    expect(overlayItem()).toStrictEqual({
      ...HOOK,
      text: "new hook",
      font_size: 72,
    });
    expect(store().history).toHaveLength(2);
  });

  it("clamps a font size of 500 to 400", () => {
    select("overlays");
    render(<PropertiesPanel />);
    const size = screen.getByRole("spinbutton", { name: "Font size" });

    commit(size, "500");

    expect(overlayItem().font_size).toBe(400);
    expect(size).toHaveValue(400);
  });

  it("refuses an 11th line break with the reason and keeps the text", () => {
    select("overlays");
    render(<PropertiesPanel />);

    commit(
      screen.getByRole("textbox", { name: "Text" }),
      Array.from({ length: 12 }, (_, i) => `line ${i}`).join("\n"),
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "At most 10 line breaks.",
    );
    expect(overlayItem()).toStrictEqual(HOOK);
    expect(store().history).toHaveLength(0);
  });

  it("caps the text at 500 characters", () => {
    select("overlays");
    render(<PropertiesPanel />);
    expect(screen.getByRole("textbox", { name: "Text" })).toHaveAttribute(
      "maxlength",
      "500",
    );
  });

  it("moves an overlay and recolours it with x/y fractions and a colour", () => {
    select("overlays");
    render(<PropertiesPanel />);
    expect(screen.getByRole("spinbutton", { name: "X" })).toHaveValue(0.1);
    expect(screen.getByRole("spinbutton", { name: "Y" })).toHaveValue(0.3);

    commit(screen.getByRole("spinbutton", { name: "X" }), "0.25");
    commit(screen.getByRole("textbox", { name: "Colour" }), "#ff0000");

    expect(overlayItem()).toStrictEqual({
      ...HOOK,
      x: 0.25,
      color: "#ff0000",
    });
  });

  it("draws the x/y values in the field colour, not the muted label colour", () => {
    // T157: the X/Y inputs sat inside the muted label, so their values read as
    // placeholders (about 3.9:1 on the panel) while Font size was full contrast.
    select("overlays");
    render(<PropertiesPanel />);
    for (const name of ["X", "Y"]) {
      const field = screen.getByRole("spinbutton", { name });
      expect(field.closest(".text-muted-foreground")).toBeNull();
    }
    expect(screen.getByText("X", { exact: true })).toHaveClass(
      "text-muted-foreground",
    );
  });

  it("shows a caption's text only, and edits it", () => {
    select("captions");
    render(<PropertiesPanel />);

    expect(screen.getByRole("textbox", { name: "Caption text" })).toHaveValue(
      "matching words",
    );
    expect(screen.queryByRole("spinbutton")).toBeNull();
    expect(screen.queryByLabelText(/font size/i)).toBeNull();
    expect(screen.queryByLabelText(/colou?r/i)).toBeNull();

    commit(
      screen.getByRole("textbox", { name: "Caption text" }),
      "matching wordz",
    );
    expect(captionItems()[0]).toStrictEqual({
      ...EDIT.tracks[1].items[0],
      text: "matching wordz",
    });
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("notes when a caption's word count no longer matches its timed words", () => {
    select("captions", 1);
    render(<PropertiesPanel />);
    expect(screen.getByRole("note")).toHaveTextContent(
      /3 words but 2 word timings/,
    );

    commit(screen.getByRole("textbox", { name: "Caption text" }), "two words");

    expect(screen.queryByRole("note")).toBeNull();
  });

  it("renders no font picker, rotation, opacity, background, alignment, weight or decoration control", () => {
    select("overlays");
    render(<PropertiesPanel />);

    for (const name of [
      /font family|^font$/i,
      /rotation/i,
      /opacity/i,
      /background/i,
      /align/i,
      /weight|bold/i,
      /decoration|underline|italic/i,
    ]) {
      expect(screen.queryByLabelText(name)).toBeNull();
      expect(screen.queryByText(name)).toBeNull();
    }
  });

  it("shows an empty state for nothing selected, a segment or a voice-over", () => {
    const { rerender } = render(<PropertiesPanel />);
    expect(
      screen.getByText(/select a caption or a text overlay/i),
    ).toBeInTheDocument();

    select("segments");
    rerender(<PropertiesPanel />);
    expect(screen.getByText(/drag its edges/i)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();

    select("voiceovers");
    rerender(<PropertiesPanel />);
    expect(screen.getByText(/voice-over panel/i)).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
