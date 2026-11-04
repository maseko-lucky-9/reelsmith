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

/** Which clock the playhead runs on: the clean source through the edit, or the rendered file. */
export type PreviewView = "source" | "result";

/** Whether split at the playhead is offered, and why not (the caller shows the reason). */
export type SplitGate = { ok: true } | { ok: false; reason: string };

/** 016's reason (TrimPanel): the rendered file does not play the edit's segments. */
export const STALE_PLAYHEAD_REASON =
  "Re-render first: the preview does not play these segments, so the playhead does not match them. Split is unavailable until the clip is re-rendered with them.";

/** A re-render is replacing the file the playhead runs on (the server records its cut as unknown meanwhile). */
export const RERENDER_IN_FLIGHT_REASON =
  "A re-render is replacing the clip: Split at the playhead waits until it finishes, or switch to the source view.";

/**
 * Split at the playhead (and keep left / keep right) is offered only while
 * the playhead is a time on the edited reel (spec 017 FR-017, data-model §2
 * `playheadOnEditClock`). The clean-source view plays the edit itself, so it
 * is always open. On the rendered file's clock (the rendered-clip view, and
 * the fallback when the source cannot play) the edit's cut must be the cut
 * that file was rendered from (`baseline`: the rendered cut, else the loaded
 * edit's cut): an unknown cut never matches, and while a re-render is in
 * flight the file is being replaced, so it waits too. No baseline (no window
 * known) leaves it open, as in 016.
 */
export function splitGate({
  view,
  edit,
  baseline,
  rerenderInFlight = false,
}: {
  view: PreviewView;
  edit: Cut;
  baseline: Cut | "unknown" | null;
  rerenderInFlight?: boolean;
}): SplitGate {
  if (view === "source") return { ok: true };
  if (rerenderInFlight) return { ok: false, reason: RERENDER_IN_FLIGHT_REASON };
  if (baseline === null) return { ok: true };
  if (baseline === "unknown" || !sameCut(edit, baseline)) {
    return { ok: false, reason: STALE_PLAYHEAD_REASON };
  }
  return { ok: true };
}
