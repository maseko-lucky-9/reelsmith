// Copied from OpenCut (MIT) apps/web/src/hooks/use-selection-box.ts @ 3037a7e
// Local changes: upstream's debug console.log calls and its unused container-relative
// rectangle in selectElementsInBox removed (noUnusedLocals); the click that follows an active
// box's release is swallowed (suppressClick) instead of upstream's justFinishedSelecting state and
// its 50 ms timeout, which only the content's click read (a row's click still cleared a same-row
// box's selection) and which outlived an unmount. Licence: ../../LICENSE-OpenCut.
import { useState, useEffect, useCallback } from "react";
import { suppressNextClick } from "@/editor/suppressClick";

interface UseSelectionBoxProps {
  containerRef: React.RefObject<HTMLElement>;
  playheadRef?: React.RefObject<HTMLElement>;
  onSelectionComplete: (
    elements: { trackId: string; elementId: string }[],
  ) => void;
  isEnabled?: boolean;
}

interface SelectionBoxState {
  startPos: { x: number; y: number };
  currentPos: { x: number; y: number };
  isActive: boolean;
}

export function useSelectionBox({
  containerRef,
  playheadRef,
  onSelectionComplete,
  isEnabled = true,
}: UseSelectionBoxProps) {
  const [selectionBox, setSelectionBox] = useState<SelectionBoxState | null>(
    null,
  );

  // Mouse down handler to start selection
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!isEnabled) return;

      // Only start selection on empty space clicks
      if ((e.target as HTMLElement).closest(".timeline-element")) {
        return;
      }
      if (playheadRef?.current?.contains(e.target as Node)) {
        return;
      }
      if ((e.target as HTMLElement).closest("[data-track-labels]")) {
        return;
      }
      // Don't start selection when clicking in the ruler area - this interferes with playhead dragging
      if ((e.target as HTMLElement).closest("[data-ruler-area]")) {
        return;
      }

      setSelectionBox({
        startPos: { x: e.clientX, y: e.clientY },
        currentPos: { x: e.clientX, y: e.clientY },
        isActive: false, // Will become active when mouse moves
      });
    },
    [isEnabled, playheadRef],
  );

  // Function to select elements within the selection box
  const selectElementsInBox = useCallback(
    (startPos: { x: number; y: number }, endPos: { x: number; y: number }) => {
      if (!containerRef.current) return;

      const container = containerRef.current;

      // Find all timeline elements within the selection rectangle
      const timelineElements = container.querySelectorAll(".timeline-element");

      const selectedElements: { trackId: string; elementId: string }[] = [];

      timelineElements.forEach((element) => {
        const elementRect = element.getBoundingClientRect();
        // Use absolute coordinates for more accurate intersection detection
        const elementAbsolute = {
          left: elementRect.left,
          top: elementRect.top,
          right: elementRect.right,
          bottom: elementRect.bottom,
        };

        const selectionAbsolute = {
          left: startPos.x,
          top: startPos.y,
          right: endPos.x,
          bottom: endPos.y,
        };

        // Normalize selection rectangle (handle dragging in any direction)
        const normalizedSelection = {
          left: Math.min(selectionAbsolute.left, selectionAbsolute.right),
          top: Math.min(selectionAbsolute.top, selectionAbsolute.bottom),
          right: Math.max(selectionAbsolute.left, selectionAbsolute.right),
          bottom: Math.max(selectionAbsolute.top, selectionAbsolute.bottom),
        };

        const elementId = element.getAttribute("data-element-id");
        const trackId = element.getAttribute("data-track-id");

        // Check if element intersects with selection rectangle (any overlap)
        // Using absolute coordinates for more precise detection
        const intersects = !(
          elementAbsolute.right < normalizedSelection.left ||
          elementAbsolute.left > normalizedSelection.right ||
          elementAbsolute.bottom < normalizedSelection.top ||
          elementAbsolute.top > normalizedSelection.bottom
        );

        if (intersects && elementId && trackId) {
          selectedElements.push({ trackId, elementId });
        }
      });

      // Always call the callback - with elements or empty array to clear selection
      onSelectionComplete(selectedElements);
    },
    [containerRef, onSelectionComplete],
  );

  // Effect to track selection box movement
  useEffect(() => {
    if (!selectionBox) return;

    const handleMouseMove = (e: MouseEvent) => {
      const deltaX = Math.abs(e.clientX - selectionBox.startPos.x);
      const deltaY = Math.abs(e.clientY - selectionBox.startPos.y);

      // Start selection if mouse moved more than 5px
      const shouldActivate = deltaX > 5 || deltaY > 5;

      const newSelectionBox = {
        ...selectionBox,
        currentPos: { x: e.clientX, y: e.clientY },
        isActive: shouldActivate || selectionBox.isActive,
      };

      setSelectionBox(newSelectionBox);

      // Real-time visual feedback: update selection as we drag
      if (newSelectionBox.isActive) {
        selectElementsInBox(
          newSelectionBox.startPos,
          newSelectionBox.currentPos,
        );
      }
    };

    const handleMouseUp = () => {
      // An active selection ends here: the click this release makes is not a click on the timeline.
      if (selectionBox?.isActive) suppressNextClick();

      // Don't call selectElementsInBox again - real-time selection already handled it
      // Just clean up the selection box visual
      setSelectionBox(null);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [selectionBox, selectElementsInBox]);

  return {
    selectionBox,
    handleMouseDown,
    isSelecting: selectionBox?.isActive || false,
  };
}
