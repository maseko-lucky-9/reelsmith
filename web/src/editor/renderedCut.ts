/**
 * The cut the clip's file was rendered from, and whether an edit plays it
 * (spec 016 FR-017; moved verbatim from `routes/clips.$clipId.edit.tsx` by
 * spec 017 T143, one source of truth for the route and the new editor).
 *
 * The rendered clip's clock (the playhead in the rendered-clip view) is a
 * time on the edited reel only while the edit's segments are the cut that
 * file was rendered from; Split is gated on `sameCut`.
 */
import type { TimelinePayload } from "@/api/client";
import {
  segmentsOf,
  upgradeTimeline,
  type SourceWindow,
} from "@/lib/timelineEdit";

export type Cut = ReadonlyArray<{ start: number; end: number }>;

/** The most two boundaries may differ and still be the same cut (the server may nudge by 1 ms). */
export const CUT_TOLERANCE = 1e-3 + 1e-9;

export function sameCut(a: Cut, b: Cut): boolean {
  return (
    a.length === b.length &&
    a.every(
      (s, i) =>
        Math.abs(s.start - b[i].start) <= CUT_TOLERANCE &&
        Math.abs(s.end - b[i].end) <= CUT_TOLERANCE,
    )
  );
}

/** The source cut a timeline plays (v1 read through the clip's window). */
export function cutOf(
  timeline: TimelinePayload,
  window: SourceWindow | null,
): Cut {
  return segmentsOf(upgradeTimeline(timeline, window) ?? timeline);
}

/**
 * The cut the clip's file was rendered from, from the server's
 * `[[start, end], ...]`: null/absent is the clip's own window (never edited,
 * or rendered before this was recorded: Split then waits for a re-render),
 * `[]` is "unknown, a render is replacing it" and matches nothing. Null when
 * there is no window to fall back to.
 */
export function renderedCut(
  rendered: ReadonlyArray<readonly [number, number]> | null | undefined,
  window: SourceWindow | null,
): Cut | "unknown" | null {
  if (Array.isArray(rendered)) {
    return rendered.length > 0
      ? rendered.map(([start, end]) => ({ start, end }))
      : "unknown";
  }
  return window ? [window] : null;
}
