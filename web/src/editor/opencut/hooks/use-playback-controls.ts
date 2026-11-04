// Adapted from OpenCut (MIT) apps/web/src/hooks/use-playback-controls.ts @ 3037a7e
// Local changes: the key map of spec 017 research R12 (Space play/pause, S split at the playhead,
// Delete/Backspace delete the selection, Cmd/Ctrl+Z undo, Shift+Cmd/Ctrl+Z and Cmd/Ctrl+Y redo);
// upstream's Cmd/Ctrl+S/Q/W/D bindings removed (the browser's save, quit, close tab; no separate
// audio). Keys are ignored while typing (input, textarea, select, contenteditable; upstream checked
// input and textarea only) and inside an open menu; Space is left to a focused button. Split acts on
// the segment under the playhead (not the selection) and only when the split gate is open (FR-017),
// else the reason is toasted; refusals from the store are toasted too. The delete, undo and redo
// handlers upstream kept in timeline.tsx live here; a multi-item delete is one undo step.
// Licence: ../../LICENSE-OpenCut.
import { useCallback, useEffect } from "react";
import { toast } from "sonner";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import {
  HISTORY_LIMIT,
  useTimelineStore,
  type EditResult,
} from "@/editor/opencut/stores/timeline-store";
import type { SplitGate } from "@/editor/renderedCut";

/** Split in two, or split and keep only one side (trim at the playhead). */
export type SplitMode = "split" | "keep-left" | "keep-right";

/** The gate when nothing restricts split (the clean-source view). */
export const SPLIT_OPEN: SplitGate = { ok: true };

const OK: EditResult = { ok: true };
const NO_SEGMENT = "Put the playhead inside a video segment to split it.";
const NOTHING_SELECTED = "Select an item on the timeline first.";

/** Toasts a refused edit's reason; returns the result. */
export function toastRefusal(result: EditResult): EditResult {
  if (!result.ok) toast.error(result.reason);
  return result;
}

/**
 * Runs store actions so that everything they change is one undo step (FR-012):
 * the history afterwards is the history before plus the state before.
 */
export function asOneUndoStep<T>(run: () => T): T {
  const before = useTimelineStore.getState();
  const snapshot = {
    tracks: before._tracks,
    captionStyle: before.captionStyle,
    timelineOrigin: before.timelineOrigin,
  };
  const result = run();
  if (useTimelineStore.getState().history !== before.history) {
    useTimelineStore.setState({
      history: [...before.history, snapshot].slice(-HISTORY_LIMIT),
    });
  }
  return result;
}

/** The segment under reel time `time` (a boundary belongs to the later segment). */
function segmentAt(time: number) {
  const track = useTimelineStore
    .getState()
    ._tracks.find((t) => t.role === "segments");
  const element = track?.elements.find(
    (el) =>
      time >= el.startTime &&
      time < el.startTime + el.duration - el.trimStart - el.trimEnd,
  );
  return track && element ? { trackId: track.id, elementId: element.id } : null;
}

/** Splits (or keeps one side of) one segment at reel time `time`; refusals carry the reason. */
export function splitElementAt(
  mode: SplitMode,
  trackId: string,
  elementId: string,
  gate: SplitGate = SPLIT_OPEN,
  time: number = usePlaybackStore.getState().currentTime,
): EditResult {
  if (!gate.ok) return gate;
  const store = useTimelineStore.getState();
  if (mode === "split") {
    const result = store.splitElement(trackId, elementId, time);
    return result.ok ? OK : result;
  }
  return mode === "keep-left"
    ? store.splitAndKeepLeft(trackId, elementId, time)
    : store.splitAndKeepRight(trackId, elementId, time);
}

/** Splits (or keeps one side of) the segment under the playhead (toolbar, S key). */
export function splitAtPlayhead(
  mode: SplitMode,
  gate: SplitGate = SPLIT_OPEN,
  time: number = usePlaybackStore.getState().currentTime,
): EditResult {
  if (!gate.ok) return gate;
  const target = segmentAt(time);
  if (!target) return { ok: false, reason: NO_SEGMENT };
  return splitElementAt(mode, target.trackId, target.elementId, gate, time);
}

/**
 * Deletes the selected items as one undo step. Other items go first: a
 * segment's removal re-derives the reel items (016 setSegments), which can
 * renumber them. The first refusal (e.g. the last segment) is returned.
 */
export function deleteSelection(): EditResult {
  const { selectedElements, _tracks } = useTimelineStore.getState();
  if (selectedElements.length === 0) {
    return { ok: false, reason: NOTHING_SELECTED };
  }
  const isSegment = (trackId: string) =>
    _tracks.find((t) => t.id === trackId)?.role === "segments";
  const ordered = [
    ...selectedElements.filter((s) => !isSegment(s.trackId)),
    ...selectedElements.filter((s) => isSegment(s.trackId)),
  ];
  let refusal: EditResult = OK;
  asOneUndoStep(() => {
    for (const { trackId, elementId } of ordered) {
      const result = useTimelineStore
        .getState()
        .removeElementFromTrack(trackId, elementId);
      if (!result.ok && refusal.ok) refusal = result;
    }
  });
  return refusal;
}

/** Typing in a field: the timeline's keys stay out of it (R12). */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  ) {
    return true;
  }
  return (
    (target as HTMLElement).isContentEditable === true ||
    target.closest('[contenteditable]:not([contenteditable="false"])') !== null
  );
}

/** Inside an open menu or list, keys belong to it. */
function isInMenu(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    target.closest('[role="menu"], [role="listbox"]') !== null
  );
}

/** Space activates a focused control; it is not play/pause there. */
function isActivatable(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    target.closest(
      'button, a[href], summary, [role="button"], [role="menuitem"], [role="checkbox"], [role="switch"], [role="tab"]',
    ) !== null
  );
}

export interface PlaybackControlsOptions {
  /** Whether split at the playhead is offered (renderedCut.splitGate); open by default. */
  splitGate?: SplitGate;
  /** Listen to the keyboard (one editor at a time). */
  enabled?: boolean;
}

export const usePlaybackControls = ({
  splitGate = SPLIT_OPEN,
  enabled = true,
}: PlaybackControlsOptions = {}) => {
  const handleKeyPress = useCallback(
    (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (isTypingTarget(e.target) || isInMenu(e.target)) {
        return;
      }

      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (e.metaKey || e.ctrlKey) {
        if (e.altKey) return;
        if (key === "z") {
          e.preventDefault();
          if (e.shiftKey) useTimelineStore.getState().redo();
          else useTimelineStore.getState().undo();
        } else if (key === "y" && !e.shiftKey) {
          e.preventDefault();
          useTimelineStore.getState().redo();
        }
        // Cmd/Ctrl+S, Q, W and D stay the browser's (R12).
        return;
      }
      if (e.altKey) return;

      switch (key) {
        case " ":
          if (isActivatable(e.target)) return;
          e.preventDefault();
          usePlaybackStore.getState().toggle();
          break;

        case "s":
          e.preventDefault();
          if (e.repeat) return;
          toastRefusal(splitAtPlayhead("split", splitGate));
          break;

        case "Delete":
        case "Backspace":
          if (useTimelineStore.getState().selectedElements.length === 0) {
            return;
          }
          e.preventDefault();
          if (e.repeat) return;
          toastRefusal(deleteSelection());
          break;
      }
    },
    [splitGate],
  );

  useEffect(() => {
    if (!enabled) return;
    document.addEventListener("keydown", handleKeyPress);
    return () => document.removeEventListener("keydown", handleKeyPress);
  }, [enabled, handleKeyPress]);
};
