// Adapted from OpenCut (MIT) apps/web/src/components/editor/properties-panel/text-properties.tsx @ 3037a7e
// Local changes: schema v2 fields only (spec 017 research R9): a caption edits its text (with a note
// when its word count no longer matches its timed words); a text overlay its text (at most 500
// characters, at most 10 line breaks), font size (an integer in [6, 400]), colour and x/y as
// fractions of the frame. No FontPicker, rotation, opacity, background, alignment, weight or
// decoration. Fields commit on blur / Enter and the slider when released (one undo step per edit,
// not per keystroke); a refusal from the store shows its reason. Base UI Slider and Textarea; the
// controls are labelled. Licence: ../../../LICENSE-OpenCut.
import type React from "react";
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Textarea } from "@/components/ui/textarea";
import { FONT_SIZE_RANGE, OVERLAY_CANVAS } from "@/editor/adapter";
import type { CaptionElement, OverlayElement } from "@/editor/types";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import {
  PropertyItem,
  PropertyItemLabel,
  PropertyItemValue,
} from "./property-item";

/** Server limits (data-model §1). */
const MAX_TEXT_CHARS = 500;
const MAX_COLOR_CHARS = 32;

type TextUpdates = Parameters<
  ReturnType<typeof useTimelineStore.getState>["updateTextElement"]
>[2];

const round4 = (n: number) => Math.round(n * 10000) / 10000;
const toFraction = (px: number, size: number) => round4(px / size + 0.5);
const isHex6 = (c: string) => /^#[0-9a-f]{6}$/i.test(c);

/** Commits on blur, and on Enter (unless Enter is a line break). */
function commitOnEnter(e: React.KeyboardEvent<HTMLElement>) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    (e.currentTarget as HTMLElement).blur();
  }
}

function wordCountNote(element: CaptionElement): string | null {
  const timed = element.origin.words;
  if (!Array.isArray(timed) || timed.length === 0) return null;
  const words = element.content.split(/\s+/).filter(Boolean).length;
  if (words === timed.length) return null;
  return `This caption has ${words} words but ${timed.length} word timings: the render spreads its words evenly over the caption.`;
}

export function TextProperties({
  element,
  trackId,
}: {
  element: CaptionElement | OverlayElement;
  trackId: string;
}) {
  const [error, setError] = useState<string | null>(null);
  const [fontDraft, setFontDraft] = useState<string | null>(null);
  const [sliderDraft, setSliderDraft] = useState<number | null>(null);
  const [colorDraft, setColorDraft] = useState<string | null>(null);
  const [xDraft, setXDraft] = useState<string | null>(null);
  const [yDraft, setYDraft] = useState<string | null>(null);

  const update = (updates: TextUpdates) => {
    const result = useTimelineStore
      .getState()
      .updateTextElement(trackId, element.id, updates);
    setError(result.ok ? null : result.reason);
    return result;
  };

  const isCaption = element.role === "captions";
  const textLabel = isCaption ? "Caption text" : "Text";
  const note = element.role === "captions" ? wordCountNote(element) : null;

  const textField = (
    <PropertyItem direction="column">
      <PropertyItemLabel>{textLabel}</PropertyItemLabel>
      <PropertyItemValue>
        <Textarea
          // Remounts with the stored text after an undo or another editor's change.
          key={element.content}
          aria-label={textLabel}
          aria-invalid={error !== null}
          defaultValue={element.content}
          maxLength={MAX_TEXT_CHARS}
          className="min-h-18 resize-none bg-background/50"
          onKeyDown={isCaption ? commitOnEnter : undefined}
          onBlur={(e) => {
            if (e.target.value !== element.content) {
              update({ content: e.target.value });
            }
          }}
        />
      </PropertyItemValue>
    </PropertyItem>
  );

  const errorLine = error ? (
    <p role="alert" className="text-xs text-destructive">
      {error}
    </p>
  ) : null;

  if (isCaption) {
    return (
      <section aria-label="Caption" className="space-y-4 p-5">
        <h3 className="text-sm font-medium">Caption</h3>
        {textField}
        {errorLine}
        {note ? (
          <p role="note" className="text-xs text-muted-foreground">
            {note}
          </p>
        ) : null}
      </section>
    );
  }

  const commitFontSize = () => {
    if (fontDraft === null) return;
    const value = Math.round(Number(fontDraft));
    setFontDraft(null);
    if (fontDraft.trim() === "" || !Number.isFinite(value)) return;
    if (value !== element.fontSize) update({ fontSize: value });
  };

  const commitColor = () => {
    if (colorDraft === null) return;
    const value = colorDraft.trim();
    setColorDraft(null);
    if (value !== "" && value !== element.color) update({ color: value });
  };

  const commitPosition = (axis: "x" | "y", draft: string | null) => {
    if (axis === "x") setXDraft(null);
    else setYDraft(null);
    if (draft === null || draft.trim() === "") return;
    const fraction = Number(draft);
    if (!Number.isFinite(fraction)) return;
    const size = axis === "x" ? OVERLAY_CANVAS.width : OVERLAY_CANVAS.height;
    const current = toFraction(element[axis], size);
    const clamped = Math.min(1, Math.max(0, fraction));
    if (clamped === current) return;
    update({ [axis]: (clamped - 0.5) * size });
  };

  const color = colorDraft ?? element.color;

  return (
    <section aria-label="Text overlay" className="space-y-6 p-5">
      <h3 className="text-sm font-medium">Text</h3>
      {textField}
      {errorLine}
      <PropertyItem direction="column">
        <PropertyItemLabel>Font size</PropertyItemLabel>
        <PropertyItemValue>
          <div className="flex items-center gap-2">
            <Slider
              aria-label="Font size slider"
              value={sliderDraft ?? element.fontSize}
              min={FONT_SIZE_RANGE[0]}
              max={FONT_SIZE_RANGE[1]}
              step={1}
              onValueChange={(v) => setSliderDraft(Array.isArray(v) ? v[0] : v)}
              onValueCommitted={(v) => {
                setSliderDraft(null);
                const value = Array.isArray(v) ? v[0] : v;
                if (value !== element.fontSize) update({ fontSize: value });
              }}
              className="w-full"
            />
            <Input
              type="number"
              aria-label="Font size"
              min={FONT_SIZE_RANGE[0]}
              max={FONT_SIZE_RANGE[1]}
              step={1}
              value={fontDraft ?? String(sliderDraft ?? element.fontSize)}
              onChange={(e) => setFontDraft(e.target.value)}
              onBlur={commitFontSize}
              onKeyDown={commitOnEnter}
              className="h-7 w-16 rounded-sm text-center text-xs! [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            />
          </div>
        </PropertyItemValue>
      </PropertyItem>
      <PropertyItem direction="row">
        <PropertyItemLabel>Colour</PropertyItemLabel>
        <PropertyItemValue className="flex items-center justify-end gap-2">
          <input
            type="color"
            aria-label="Colour picker"
            value={isHex6(color) ? color : "#ffffff"}
            onChange={(e) => setColorDraft(e.target.value)}
            onBlur={commitColor}
            className="h-7 w-9 shrink-0 cursor-pointer rounded-sm border border-input bg-transparent"
          />
          <Input
            aria-label="Colour"
            value={color}
            maxLength={MAX_COLOR_CHARS}
            onChange={(e) => setColorDraft(e.target.value)}
            onBlur={commitColor}
            onKeyDown={commitOnEnter}
            className="h-7 w-28 text-xs"
          />
        </PropertyItemValue>
      </PropertyItem>
      <PropertyItem direction="column">
        <PropertyItemLabel>Position (fractions of the frame)</PropertyItemLabel>
        <PropertyItemValue>
          <div className="grid grid-cols-2 gap-2">
            {(["x", "y"] as const).map((axis) => {
              const size =
                axis === "x" ? OVERLAY_CANVAS.width : OVERLAY_CANVAS.height;
              const draft = axis === "x" ? xDraft : yDraft;
              const setDraft = axis === "x" ? setXDraft : setYDraft;
              return (
                <label key={axis} className="flex items-center gap-1.5 text-xs">
                  {/* Only the axis letter is muted: the value is drawn at full contrast, like Font size. */}
                  <span className="text-muted-foreground">
                    {axis.toUpperCase()}
                  </span>
                  <Input
                    type="number"
                    aria-label={axis.toUpperCase()}
                    min={0}
                    max={1}
                    step={0.01}
                    value={draft ?? String(toFraction(element[axis], size))}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => commitPosition(axis, draft)}
                    onKeyDown={commitOnEnter}
                    className="h-7 text-xs"
                  />
                </label>
              );
            })}
          </div>
        </PropertyItemValue>
      </PropertyItem>
    </section>
  );
}
