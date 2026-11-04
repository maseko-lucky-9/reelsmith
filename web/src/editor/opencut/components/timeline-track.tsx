// Adapted from OpenCut (MIT) apps/web/src/components/editor/timeline-track.tsx @ 3037a7e
// Local changes: no media store, no project store (a constant 30 fps), no drops (no media or text
// drag-and-drop, no track creation on drop); the dragged element's track ends the drag and finds
// the row under the pointer itself (upstream let every track race on mouseup); a move to another
// track goes through moveElementToTrack, which refuses other roles (FR-009: items move only within
// tracks of their own kind; the reason is toasted and the item moves in time on its own track); a
// move between tracks is one undo step; the drag preview stays on the reel; right and middle
// clicks never start a drag; the store refuses drags of segments (ripple) and captions (speech
// timing). Rows carry data-track-row / data-track-id / data-track-role. Licence: ../../LICENSE-OpenCut.
import type React from "react";
import { useEffect, useRef, useState } from "react";
import type { SplitGate } from "@/editor/renderedCut";
import type { EditorElement, EditorTrack } from "@/editor/types";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import {
  snapTimeToFrame,
  TIMELINE_CONSTANTS,
} from "@/editor/opencut/constants/timeline-constants";
import { TIMELINE_FPS } from "@/editor/opencut/hooks/use-timeline-playhead";
import {
  asOneUndoStep,
  toastRefusal,
} from "@/editor/opencut/hooks/use-playback-controls";
import { TimelineElement } from "./timeline-element";

const OVERLAP =
  "Cannot move it here: it would overlap another item on that track.";

const visible = (el: EditorElement) => el.duration - el.trimStart - el.trimEnd;

/** The track row under the pointer (rows of the same timeline). */
function rowAt(origin: HTMLElement | null, clientY: number) {
  const scope = origin?.closest("[data-timeline]") ?? document;
  const rows = Array.from(
    scope.querySelectorAll<HTMLElement>("[data-track-row]"),
  );
  const row = rows.find((r) => {
    const rect = r.getBoundingClientRect();
    return clientY >= rect.top && clientY <= rect.bottom;
  });
  return row?.dataset.trackId ?? null;
}

function overlaps(
  track: EditorTrack,
  movingId: string,
  start: number,
  length: number,
) {
  return track.elements.some((existing) => {
    if (existing.id === movingId) return false;
    const existingEnd = existing.startTime + visible(existing);
    return start < existingEnd && start + length > existing.startTime;
  });
}

export function TimelineTrackContent({
  track,
  zoomLevel,
  splitGate,
}: {
  track: EditorTrack;
  zoomLevel: number;
  splitGate?: SplitGate;
}) {
  const selectedElements = useTimelineStore((s) => s.selectedElements);
  const dragState = useTimelineStore((s) => s.dragState);

  const timelineRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const [mouseDownLocation, setMouseDownLocation] = useState<{
    x: number;
    y: number;
  } | null>(null);

  const isDragOrigin = dragState.isDragging && dragState.trackId === track.id;

  // Set up mouse event listeners for a drag this track started
  useEffect(() => {
    if (!isDragOrigin || !dragState.elementId) return;
    const elementId = dragState.elementId;
    const moving = track.elements.find((el) => el.id === elementId);
    if (!moving) return;
    const length = visible(moving);

    const timeAt = (clientX: number) => {
      const rect = timelineRef.current?.getBoundingClientRect();
      const mouseX = clientX - (rect?.left ?? 0);
      const mouseTime = Math.max(
        0,
        mouseX / (TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel),
      );
      const adjustedTime = Math.max(0, mouseTime - dragState.clickOffsetTime);
      const reelEnd = useTimelineStore.getState().getReelDuration();
      // Frame-snapped, and never past the reel's end (the store clamps the same way).
      return Math.min(
        Math.max(0, reelEnd - length),
        snapTimeToFrame(adjustedTime, TIMELINE_FPS),
      );
    };

    const handleMouseMove = (e: MouseEvent) => {
      const store = useTimelineStore.getState();
      // On first mouse move during drag, ensure the element is selected
      const isSelected = store.selectedElements.some(
        (c) => c.trackId === track.id && c.elementId === elementId,
      );
      if (!isSelected) store.selectElement(track.id, elementId, false);
      store.updateDragTime(timeAt(e.clientX));
    };

    const handleMouseUp = (e: MouseEvent) => {
      const store = useTimelineStore.getState();
      const finalTime = store.dragState.currentTime;
      store.endDrag();
      const targetId = rowAt(rowRef.current, e.clientY) ?? track.id;
      const target = store._tracks.find((t) => t.id === targetId);

      if (target && target.id !== track.id) {
        if (overlaps(target, elementId, finalTime, length)) {
          toastRefusal({ ok: false, reason: OVERLAP });
          return;
        }
        const moved = asOneUndoStep(() => {
          const result = toastRefusal(
            store.moveElementToTrack(track.id, target.id, elementId),
          );
          if (result.ok) {
            useTimelineStore
              .getState()
              .updateElementStartTime(target.id, elementId, finalTime);
          }
          return result.ok;
        });
        if (moved) return;
      }

      // Same track, or a track of another kind: move in time on this track.
      if (finalTime === moving.startTime) return;
      if (overlaps(track, elementId, finalTime, length)) {
        toastRefusal({ ok: false, reason: OVERLAP });
        return;
      }
      toastRefusal(
        useTimelineStore
          .getState()
          .updateElementStartTime(track.id, elementId, finalTime),
      );
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [
    isDragOrigin,
    dragState.elementId,
    dragState.clickOffsetTime,
    track,
    zoomLevel,
  ]);

  const selectElement = (element: EditorElement, multi: boolean) =>
    useTimelineStore.getState().selectElement(track.id, element.id, multi);

  const handleElementMouseDown = (
    e: React.MouseEvent,
    element: EditorElement,
  ) => {
    // Right and middle clicks never start a drag (the context menu selects).
    if (e.button !== 0) return;
    setMouseDownLocation({ x: e.clientX, y: e.clientY });

    const isMultiSelect = e.metaKey || e.ctrlKey || e.shiftKey;
    // Handle multi-selection for left-click with modifiers
    if (isMultiSelect) {
      selectElement(element, true);
    }

    // Calculate the offset from the left edge of the element to where the user clicked
    const box =
      (e.currentTarget as HTMLElement).closest(".timeline-element") ??
      (e.currentTarget as HTMLElement);
    const elementRect = box.getBoundingClientRect();
    const clickOffsetX = e.clientX - elementRect.left;
    const clickOffsetTime =
      clickOffsetX / (TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel);

    // The store refuses segments (they ripple) and captions (speech timing).
    useTimelineStore
      .getState()
      .startDrag(
        element.id,
        track.id,
        e.clientX,
        element.startTime,
        clickOffsetTime,
      );
  };

  const handleElementClick = (e: React.MouseEvent, element: EditorElement) => {
    e.stopPropagation();

    // Check if mouse moved significantly
    if (mouseDownLocation) {
      const deltaX = Math.abs(e.clientX - mouseDownLocation.x);
      const deltaY = Math.abs(e.clientY - mouseDownLocation.y);
      // If it moved more than a few pixels, consider it a drag and not a click.
      if (deltaX > 5 || deltaY > 5) {
        setMouseDownLocation(null); // Reset for next interaction
        return;
      }
    }

    // Skip selection logic for multi-selection (handled in mousedown)
    if (e.metaKey || e.ctrlKey || e.shiftKey) {
      return;
    }

    // Handle single selection
    const isSelected = selectedElements.some(
      (c) => c.trackId === track.id && c.elementId === element.id,
    );

    if (!isSelected) {
      // If element is not selected, select it (replacing other selections)
      selectElement(element, false);
    }
    // If element is already selected, keep it selected (do nothing)
  };

  return (
    <div
      ref={rowRef}
      className="h-full w-full hover:bg-muted/20"
      data-track-row
      data-track-id={track.id}
      data-track-role={track.role}
      onClick={(e) => {
        // If clicking empty area (not on an element), deselect all elements
        if (!(e.target as HTMLElement).closest(".timeline-element")) {
          useTimelineStore.getState().clearSelectedElements();
        }
      }}
    >
      <div
        ref={timelineRef}
        className="track-elements-container relative h-full min-w-full"
      >
        {track.elements.length === 0 ? (
          <div className="h-full w-full rounded-sm border border-dashed border-muted-foreground/30" />
        ) : (
          track.elements.map((element) => {
            const isSelected = selectedElements.some(
              (c) => c.trackId === track.id && c.elementId === element.id,
            );
            return (
              <TimelineElement
                key={element.id}
                element={element}
                track={track}
                zoomLevel={zoomLevel}
                isSelected={isSelected}
                splitGate={splitGate}
                onElementMouseDown={handleElementMouseDown}
                onElementClick={handleElementClick}
                onElementSelect={selectElement}
              />
            );
          })
        )}
      </div>
    </div>
  );
}
