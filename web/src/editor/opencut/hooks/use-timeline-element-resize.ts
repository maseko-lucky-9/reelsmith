// Adapted from OpenCut (MIT) apps/web/src/hooks/use-timeline-element-resize.ts @ 3037a7e
// Local changes: no media store. Edge drags preview locally and commit once on release (upstream
// wrote the store on every mouse move, one history entry each). A segment's edges are source
// seconds: they stop at the source's bounds and the neighbours' source windows, keep 0.05 s, and
// commit through trimSegment (016 setSegments: reel items follow). A text overlay's left edge moves
// its start and keeps its end, its right edge its end, inside the reel; both in one undo step.
// Captions (their timing is the speech) and voice-overs (their length is the audio) get no
// handles. Licence: ../../LICENSE-OpenCut.
import { useEffect, useState } from "react";
import { MIN_SEGMENT_SECONDS } from "@/lib/timelineEdit";
import type { EditorElement, EditorTrack } from "@/editor/types";
import { TIMELINE_CONSTANTS } from "@/editor/opencut/constants/timeline-constants";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import {
  asOneUndoStep,
  toastRefusal,
} from "@/editor/opencut/hooks/use-playback-controls";

interface UseTimelineElementResizeProps {
  element: EditorElement;
  track: EditorTrack;
  zoomLevel: number;
}

type Side = "left" | "right";

/** An edge drag in the element's own clock (source s for a segment, reel s for an overlay). */
interface ResizeDrag {
  elementId: string;
  side: Side;
  startX: number;
  /** The edges when the drag started. */
  initial: { start: number; end: number };
  /** How far each edge may go. */
  bounds: { lo: number; hi: number };
  /** The edges now. */
  edges: { start: number; end: number };
}

/** Where the element is drawn while an edge is dragged (reel seconds). */
export interface ResizePreview {
  startTime: number;
  visibleDuration: number;
}

const visible = (el: EditorElement) => el.duration - el.trimStart - el.trimEnd;

function canResize(element: EditorElement, readOnly: boolean): boolean {
  return (
    !readOnly && (element.role === "segments" || element.role === "overlays")
  );
}

/** The element's edges and how far they may go, from the store as it is now. */
function dragFrame(element: EditorElement) {
  const state = useTimelineStore.getState();
  if (element.role === "segments") {
    const segments =
      state._tracks.find((t) => t.role === "segments")?.elements ?? [];
    const i = segments.findIndex((el) => el.id === element.id);
    const prev = i > 0 ? segments[i - 1] : undefined;
    const next = i >= 0 ? segments[i + 1] : undefined;
    return {
      initial: {
        start: element.trimStart,
        end: element.duration - element.trimEnd,
      },
      bounds: {
        lo: prev ? prev.duration - prev.trimEnd : 0,
        hi: next ? next.trimStart : element.duration,
      },
    };
  }
  return {
    initial: {
      start: element.startTime,
      end: element.startTime + visible(element),
    },
    bounds: { lo: 0, hi: state.getReelDuration() },
  };
}

function edgesAt(drag: ResizeDrag, clientX: number, zoomLevel: number) {
  const delta =
    (clientX - drag.startX) /
    (TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel);
  const { initial, bounds } = drag;
  if (drag.side === "left") {
    const start = Math.min(
      initial.end - MIN_SEGMENT_SECONDS,
      Math.max(bounds.lo, initial.start + delta),
    );
    return { start, end: initial.end };
  }
  const end = Math.max(
    initial.start + MIN_SEGMENT_SECONDS,
    Math.min(bounds.hi, initial.end + delta),
  );
  return { start: initial.start, end };
}

/** One store change for the dragged edges (one undo step). */
function commit(element: EditorElement, trackId: string, drag: ResizeDrag) {
  const { edges, initial } = drag;
  if (edges.start === initial.start && edges.end === initial.end) return;
  const store = useTimelineStore.getState();
  if (element.role === "segments") {
    toastRefusal(
      store.trimSegment(
        element.id,
        drag.side === "left" ? { start: edges.start } : { end: edges.end },
      ),
    );
    return;
  }
  const trims = element.trimStart + element.trimEnd;
  const duration = edges.end - edges.start + trims;
  if (drag.side === "right") {
    toastRefusal(store.updateElementDuration(trackId, element.id, duration));
    return;
  }
  // The left edge moves the start and keeps the end: shrink before moving later,
  // move before growing earlier, so neither step is clamped by the reel's end.
  asOneUndoStep(() => {
    const s = useTimelineStore.getState();
    if (edges.start > initial.start) {
      toastRefusal(s.updateElementDuration(trackId, element.id, duration));
      toastRefusal(
        useTimelineStore
          .getState()
          .updateElementStartTime(trackId, element.id, edges.start),
      );
    } else {
      toastRefusal(s.updateElementStartTime(trackId, element.id, edges.start));
      toastRefusal(
        useTimelineStore
          .getState()
          .updateElementDuration(trackId, element.id, duration),
      );
    }
  });
}

export function useTimelineElementResize({
  element,
  track,
  zoomLevel,
}: UseTimelineElementResizeProps) {
  const [resizing, setResizing] = useState<ResizeDrag | null>(null);
  const readOnly = useTimelineStore((s) => s.readOnly !== null);
  const resizable = canResize(element, readOnly);

  // Set up document-level mouse listeners during resize (like proper drag behavior)
  useEffect(() => {
    if (!resizing) return;

    const handleDocumentMouseMove = (e: MouseEvent) => {
      setResizing((drag) =>
        drag ? { ...drag, edges: edgesAt(drag, e.clientX, zoomLevel) } : drag,
      );
    };

    const handleDocumentMouseUp = (e: MouseEvent) => {
      commit(element, track.id, {
        ...resizing,
        edges: edgesAt(resizing, e.clientX, zoomLevel),
      });
      setResizing(null);
    };

    // Add document-level listeners for proper drag behavior
    document.addEventListener("mousemove", handleDocumentMouseMove);
    document.addEventListener("mouseup", handleDocumentMouseUp);

    return () => {
      document.removeEventListener("mousemove", handleDocumentMouseMove);
      document.removeEventListener("mouseup", handleDocumentMouseUp);
    };
  }, [resizing, zoomLevel, element, track.id]);

  const handleResizeStart = (
    e: React.MouseEvent,
    elementId: string,
    side: Side,
  ) => {
    e.stopPropagation();
    e.preventDefault();
    if (!resizable) return;
    const { initial, bounds } = dragFrame(element);
    setResizing({
      elementId,
      side,
      startX: e.clientX,
      initial,
      bounds,
      edges: initial,
    });
  };

  let preview: ResizePreview | null = null;
  if (resizing) {
    const { start, end } = resizing.edges;
    preview = {
      // A segment's reel place is derived (back to back): only its length previews.
      startTime: element.role === "segments" ? element.startTime : start,
      visibleDuration: end - start,
    };
  }

  return {
    resizing,
    isResizing: resizing !== null,
    /** Which edges have a handle. */
    handles: { left: resizable, right: resizable },
    preview,
    handleResizeStart,
  };
}
