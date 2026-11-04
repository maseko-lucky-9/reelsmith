// Adapted from OpenCut (MIT) apps/web/src/components/editor/timeline-playhead.tsx @ 3037a7e
// Local changes: presentational: the timeline owns use-timeline-playhead (one scrub state for the
// ruler and the playhead, so upstream's useTimelinePlayheadRuler wrapper and default export are
// gone) and passes the position and handlers in; no refs read during render (React 19 / compiler
// lint): the line spans the container by CSS and the labels' width is a prop instead of
// offsetHeight/offsetWidth; keyboard operable as a slider (arrow keys a frame, Shift a second,
// Home/End); Tailwind 4 renames (shadow-xs, z-100); the line is drawn at its time minus the tracks'
// horizontal scroll and hidden while scrolled out of their view (upstream ignored the scroll, so the
// line ran off screen and auto-scroll pushed it further). Licence: ../../LICENSE-OpenCut.
import type React from "react";
import type { RefObject } from "react";
import { TIMELINE_CONSTANTS } from "@/editor/opencut/constants/timeline-constants";

interface TimelinePlayheadProps {
  /** Where the playhead is drawn (the scrub position while scrubbing), reel seconds. */
  position: number;
  duration: number;
  zoomLevel: number;
  /** Pixels between the timeline's left edge and the reel's 0 s (the track labels). */
  trackLabelsWidth: number;
  /** How far the tracks are scrolled horizontally, px. */
  scrollLeft?: number;
  /** The tracks' visible width, px; 0 while unknown (nothing is hidden on the right). */
  viewportWidth?: number;
  playheadRef?: RefObject<HTMLDivElement | null>;
  onMouseDown: (e: React.MouseEvent) => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
}

const seconds = (n: number) => `${n.toFixed(2)} s`;

export function TimelinePlayhead({
  position,
  duration,
  zoomLevel,
  trackLabelsWidth,
  scrollLeft = 0,
  viewportWidth = 0,
  playheadRef,
  onMouseDown,
  onKeyDown,
}: TimelinePlayheadProps) {
  // Where the line is in the tracks' view: the scrolled-off content is not on screen.
  const x =
    position * TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel - scrollLeft;
  const leftPosition = trackLabelsWidth + x;
  // Out of view it is hidden, not drawn over the labels or past the timeline (where focusing
  // it would scroll the clipped container); a seek scrolls it back into view.
  const outOfView = x < 0 || (viewportWidth > 0 && x > viewportWidth);

  return (
    <div
      ref={playheadRef}
      role="slider"
      tabIndex={0}
      aria-label="Playhead"
      aria-valuemin={0}
      aria-valuemax={Number(duration.toFixed(3))}
      aria-valuenow={Number(position.toFixed(3))}
      aria-valuetext={`${seconds(position)} of ${seconds(duration)}`}
      className="pointer-events-auto absolute top-0 bottom-2 z-100 outline-none focus-visible:[&>span]:ring-2 focus-visible:[&>span]:ring-ring"
      style={{
        left: `${leftPosition}px`,
        width: "2px", // Slightly wider for better click target
        visibility: outOfView ? "hidden" : undefined,
      }}
      onMouseDown={onMouseDown}
      onKeyDown={onKeyDown}
    >
      {/* The line spanning full height */}
      <div className="absolute left-0 h-full w-0.5 cursor-col-resize bg-foreground" />

      {/* Dot indicator at the top (in ruler area) */}
      <span className="absolute top-1 left-1/2 h-3 w-3 -translate-x-1/2 rounded-full border-2 border-foreground bg-foreground shadow-xs" />
    </div>
  );
}
