// Adapted from OpenCut (MIT) apps/web/src/hooks/use-timeline-playhead.ts @ 3037a7e
// Local changes: a constant 30 fps (TIMELINE_FPS) instead of the project store's; React 19 ref
// types; the scroll viewports are Base UI's ([data-slot="scroll-area-viewport"], not Radix's);
// PIXELS_PER_SECOND instead of a literal 50; callbacks declared before use with full deps;
// new handlePlayheadKeyDown (arrow keys move a frame, Shift a second, Home/End) so the playhead
// is keyboard operable. Licence: ../../LICENSE-OpenCut.
import { useState, useEffect, useCallback, type RefObject } from "react";
import {
  snapTimeToFrame,
  TIMELINE_CONSTANTS,
} from "@/editor/opencut/constants/timeline-constants";

/** Reelsmith has no project frame rate: the playhead snaps to 1/30 s (upstream's default). */
export const TIMELINE_FPS = 30;

/** Base UI's scroll-area viewport (components/ui/scroll-area.tsx). */
export const SCROLL_VIEWPORT_SELECTOR = '[data-slot="scroll-area-viewport"]';

interface UseTimelinePlayheadProps {
  currentTime: number;
  duration: number;
  zoomLevel: number;
  seek: (time: number) => void;
  rulerRef: RefObject<HTMLDivElement | null>;
  rulerScrollRef: RefObject<HTMLDivElement | null>;
  tracksScrollRef: RefObject<HTMLDivElement | null>;
  playheadRef?: RefObject<HTMLDivElement | null>;
}

export function useTimelinePlayhead({
  currentTime,
  duration,
  zoomLevel,
  seek,
  rulerRef,
  rulerScrollRef,
  tracksScrollRef,
  playheadRef,
}: UseTimelinePlayheadProps) {
  // Playhead scrubbing state
  const [isScrubbing, setIsScrubbing] = useState(false);
  const [scrubTime, setScrubTime] = useState<number | null>(null);

  // Ruler drag detection state
  const [isDraggingRuler, setIsDraggingRuler] = useState(false);
  const [hasDraggedRuler, setHasDraggedRuler] = useState(false);

  const playheadPosition =
    isScrubbing && scrubTime !== null ? scrubTime : currentTime;

  const handleScrub = useCallback(
    (e: MouseEvent | React.MouseEvent) => {
      const ruler = rulerRef.current;
      if (!ruler) return;
      const rect = ruler.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const rawTime = Math.max(
        0,
        Math.min(
          duration,
          x / (TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel),
        ),
      );
      // Use frame snapping for playhead scrubbing
      const time = snapTimeToFrame(rawTime, TIMELINE_FPS);
      setScrubTime(time);
      seek(time); // update video preview in real time
    },
    [duration, zoomLevel, seek, rulerRef],
  );

  // --- Playhead Scrubbing Handlers ---
  const handlePlayheadMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation(); // Prevent ruler drag from triggering
      setIsScrubbing(true);
      handleScrub(e);
    },
    [handleScrub],
  );

  // Ruler mouse down handler
  const handleRulerMouseDown = useCallback(
    (e: React.MouseEvent) => {
      // Only handle left mouse button
      if (e.button !== 0) return;

      // Don't interfere if clicking on the playhead itself
      if (playheadRef?.current?.contains(e.target as Node)) return;

      e.preventDefault();
      setIsDraggingRuler(true);
      setHasDraggedRuler(false);

      // Start scrubbing immediately
      setIsScrubbing(true);
      handleScrub(e);
    },
    [handleScrub, playheadRef],
  );

  // Keyboard: a frame per arrow, a second with Shift, Home/End for the ends of the reel.
  const handlePlayheadKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const frame = 1 / TIMELINE_FPS;
      const step = e.shiftKey ? 1 : frame;
      let target: number | null = null;
      if (e.key === "ArrowRight") target = currentTime + step;
      else if (e.key === "ArrowLeft") target = currentTime - step;
      else if (e.key === "Home") target = 0;
      else if (e.key === "End") target = duration;
      if (target === null) return;
      e.preventDefault();
      const clamped = Math.max(0, Math.min(duration, target));
      seek(
        clamped === duration
          ? duration
          : snapTimeToFrame(clamped, TIMELINE_FPS),
      );
    },
    [currentTime, duration, seek],
  );

  // Mouse move/up event handlers
  useEffect(() => {
    if (!isScrubbing) return;
    const onMouseMove = (e: MouseEvent) => {
      handleScrub(e);
      // Mark that we've dragged if ruler drag is active
      if (isDraggingRuler) {
        setHasDraggedRuler(true);
      }
    };
    const onMouseUp = (e: MouseEvent) => {
      setIsScrubbing(false);
      if (scrubTime !== null) seek(scrubTime); // finalize seek
      setScrubTime(null);

      // Handle ruler click vs drag
      if (isDraggingRuler) {
        setIsDraggingRuler(false);
        // If we didn't drag, treat it as a click-to-seek
        if (!hasDraggedRuler) {
          handleScrub(e);
        }
        setHasDraggedRuler(false);
      }
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [
    isScrubbing,
    scrubTime,
    seek,
    handleScrub,
    isDraggingRuler,
    hasDraggedRuler,
  ]);

  // --- Playhead auto-scroll effect ---
  useEffect(() => {
    const rulerViewport = rulerScrollRef.current?.querySelector(
      SCROLL_VIEWPORT_SELECTOR,
    ) as HTMLElement | null;
    const tracksViewport = tracksScrollRef.current?.querySelector(
      SCROLL_VIEWPORT_SELECTOR,
    ) as HTMLElement | null;
    if (!rulerViewport || !tracksViewport) return;
    const playheadPx =
      playheadPosition * TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel;
    const viewportWidth = rulerViewport.clientWidth;
    const scrollMin = 0;
    const scrollMax = rulerViewport.scrollWidth - viewportWidth;
    // Center the playhead if it's not visible (100px buffer)
    const desiredScroll = Math.max(
      scrollMin,
      Math.min(scrollMax, playheadPx - viewportWidth / 2),
    );
    if (
      playheadPx < rulerViewport.scrollLeft + 100 ||
      playheadPx > rulerViewport.scrollLeft + viewportWidth - 100
    ) {
      rulerViewport.scrollLeft = tracksViewport.scrollLeft = desiredScroll;
    }
  }, [playheadPosition, duration, zoomLevel, rulerScrollRef, tracksScrollRef]);

  return {
    playheadPosition,
    handlePlayheadMouseDown,
    handleRulerMouseDown,
    handlePlayheadKeyDown,
    isDraggingRuler,
  };
}
