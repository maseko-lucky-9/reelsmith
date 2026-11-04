// Adapted from OpenCut (MIT) apps/web/src/components/editor/timeline.tsx @ 3037a7e
// Local changes: no media or project store and no drops (no media import, no processMediaFiles,
// no "Add Test Clip"); Radix to the Base UI wrappers (components/ui), Tailwind 4 renames (R14);
// the CommonJS require of timeline-constants is an import and the frame rate a constant 30 fps;
// the toolbar is play/pause, the time, Split, Split keep left, Split keep right, Delete, Undo,
// Redo, Zoom out and Zoom in (no duplicate, freeze frame, separate audio): split acts on the
// segment under the playhead and waits on the split gate, whose reason the disabled controls
// carry; the delete/undo/redo key handlers moved to use-playback-controls (R12 key map, mounted
// here); the playback duration is the edited reel's length (or `playbackDuration`, the rendered
// clip's in the result view) and the content is exactly that wide (no +30 s buffer; CSS keeps it
// at least the viewport); one playhead hook for the ruler and the
// playhead; track labels name the track (Video / Captions / Text / Voice-over) and the Video track
// has the mute-source toggle (upstream's track context menu, whose only applicable item it was);
// Base UI scroll viewports; the playhead is drawn at the tracks' scroll offset (upstream ignored it);
// the click that ends an edge resize or a selection box is swallowed (suppressClick), and a portaled
// menu's click (bubbled through React) neither seeks nor is swallowed; a read-only edit shows why.
// Licence: ../../LICENSE-OpenCut.
import type React from "react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  ArrowLeftToLine,
  ArrowRightToLine,
  Captions,
  Mic,
  Pause,
  Play,
  Redo2,
  Scissors,
  Trash2,
  Type,
  Undo2,
  Video,
  Volume2,
  VolumeX,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { SplitGate } from "@/editor/renderedCut";
import type { EditorTrack } from "@/editor/types";
import { usePlaybackStore } from "@/editor/opencut/stores/playback-store";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { useTimelineZoom } from "@/editor/opencut/hooks/use-timeline-zoom";
import { useSelectionBox } from "@/editor/opencut/hooks/use-selection-box";
import {
  TIMELINE_FPS,
  useTimelinePlayhead,
} from "@/editor/opencut/hooks/use-timeline-playhead";
import {
  deleteSelection,
  SPLIT_OPEN,
  splitAtPlayhead,
  toastRefusal,
  usePlaybackControls,
  type SplitMode,
} from "@/editor/opencut/hooks/use-playback-controls";
import {
  getCumulativeHeightBefore,
  getTotalTracksHeight,
  getTrackHeight,
  snapTimeToFrame,
  TIMELINE_CONSTANTS,
} from "@/editor/opencut/constants/timeline-constants";
import { takeSuppressedClick } from "@/editor/suppressClick";
import { SelectionBox } from "./selection-box";
import { TimelinePlayhead } from "./timeline-playhead";
import { TimelineTrackContent } from "./timeline-track";

/** The track labels' column (upstream w-48); the playhead is offset by it. */
const TRACK_LABELS_PX = 192;
/** The zoom hook's bounds. */
const ZOOM_MIN = 0.1;
const ZOOM_MAX = 10;

interface TimelineProps {
  /** Whether split at the playhead is offered (renderedCut.splitGate); open by default. */
  splitGate?: SplitGate;
  /** Listen to the keyboard map (R12); one editor at a time. */
  keyboard?: boolean;
  /**
   * The playhead's clock length when it is not the edited reel's: the rendered
   * clip's in the result view. Absent: the reel's length.
   */
  playbackDuration?: number;
  className?: string;
}

/** Ruler marks: an interval readable at the zoom level, and its label. */
function getTimeInterval(zoom: number) {
  const pixelsPerSecond = TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoom;
  if (pixelsPerSecond >= 200) return 0.1; // Every 0.1s when very zoomed in
  if (pixelsPerSecond >= 100) return 0.5; // Every 0.5s when zoomed in
  if (pixelsPerSecond >= 50) return 1; // Every 1s at normal zoom
  if (pixelsPerSecond >= 25) return 2; // Every 2s when zoomed out
  if (pixelsPerSecond >= 12) return 5; // Every 5s when more zoomed out
  if (pixelsPerSecond >= 6) return 10; // Every 10s when very zoomed out
  return 30; // Every 30s when extremely zoomed out
}

function formatMark(seconds: number, interval: number) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, "0")}:${Math.floor(secs).toString().padStart(2, "0")}`;
  } else if (minutes > 0) {
    return `${minutes}:${Math.floor(secs).toString().padStart(2, "0")}`;
  } else if (interval >= 1) {
    return `${Math.floor(secs)}s`;
  }
  return `${secs.toFixed(1)}s`;
}

function ToolbarButton({
  label,
  hint,
  blockedReason,
  disabled,
  onClick,
  children,
}: {
  label: string;
  hint: string;
  /** Why it is disabled, shown as its description and native title. */
  blockedReason?: string | null;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const reasonId = useId();
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={label}
            aria-describedby={blockedReason ? reasonId : undefined}
            title={blockedReason ?? undefined}
            disabled={disabled}
            onClick={onClick}
          />
        }
      >
        {children}
        {blockedReason ? (
          <span id={reasonId} className="sr-only">
            {blockedReason}
          </span>
        ) : null}
      </TooltipTrigger>
      <TooltipContent>{blockedReason ?? hint}</TooltipContent>
    </Tooltip>
  );
}

export function Timeline({
  splitGate = SPLIT_OPEN,
  keyboard = true,
  playbackDuration,
  className,
}: TimelineProps) {
  // Timeline shows the edit's tracks (segments, captions, text, voice-overs) and their elements.
  // Elements can be selected, split, trimmed, moved and deleted.
  const tracks = useTimelineStore((s) => s.tracks);
  const selectedElements = useTimelineStore((s) => s.selectedElements);
  const setSelectedElements = useTimelineStore((s) => s.setSelectedElements);
  const clearSelectedElements = useTimelineStore(
    (s) => s.clearSelectedElements,
  );
  const canUndo = useTimelineStore((s) => s.history.length > 0);
  const canRedo = useTimelineStore((s) => s.redoStack.length > 0);
  const readOnly = useTimelineStore((s) => s.readOnly);
  const reelDuration = useTimelineStore((s) => s.getReelDuration());
  const currentTime = usePlaybackStore((s) => s.currentTime);
  const duration = usePlaybackStore((s) => s.duration);
  const isPlaying = usePlaybackStore((s) => s.isPlaying);
  const seek = usePlaybackStore((s) => s.seek);
  const setDuration = usePlaybackStore((s) => s.setDuration);
  const toggle = usePlaybackStore((s) => s.toggle);

  const timelineRef = useRef<HTMLDivElement>(null);
  const rulerRef = useRef<HTMLDivElement>(null);
  const [isInTimeline, setIsInTimeline] = useState(false);

  usePlaybackControls({ splitGate, enabled: keyboard });

  // Timeline zoom functionality
  const { zoomLevel, setZoomLevel, handleWheel } = useTimelineZoom({
    containerRef: timelineRef,
    isInTimeline,
  });

  // The reel exactly: its length at the zoom level (CSS keeps it at least the viewport wide).
  const contentWidth =
    Math.max(0, duration) * TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel;

  // Scroll synchronization and auto-scroll to playhead
  const rulerScrollRef = useRef<HTMLDivElement>(null);
  const tracksScrollRef = useRef<HTMLDivElement>(null);
  const rulerViewportRef = useRef<HTMLDivElement>(null);
  const tracksViewportRef = useRef<HTMLDivElement>(null);
  const trackLabelsViewportRef = useRef<HTMLDivElement>(null);
  const playheadRef = useRef<HTMLDivElement>(null);
  const isUpdatingRef = useRef(false);
  // The tracks' horizontal scroll and visible width: where the playhead line is drawn.
  const [tracksView, setTracksView] = useState({ scrollLeft: 0, width: 0 });
  const lastRulerSync = useRef(0);
  const lastTracksSync = useRef(0);
  const lastVerticalSync = useRef(0);

  // One playhead hook: the ruler and the playhead share its scrub state.
  const {
    playheadPosition,
    handlePlayheadMouseDown,
    handleRulerMouseDown,
    handlePlayheadKeyDown,
  } = useTimelinePlayhead({
    currentTime,
    duration,
    zoomLevel,
    seek,
    rulerRef,
    rulerScrollRef,
    tracksScrollRef,
    playheadRef,
  });

  // Selection box functionality
  const tracksContainerRef = useRef<HTMLDivElement>(null);
  const {
    selectionBox,
    handleMouseDown: handleSelectionMouseDown,
    isSelecting,
  } = useSelectionBox({
    // The copied hook keeps React 18's RefObject<HTMLElement> types.
    containerRef: tracksContainerRef as React.RefObject<HTMLElement>,
    playheadRef: playheadRef as React.RefObject<HTMLElement>,
    onSelectionComplete: setSelectedElements,
  });

  // Timeline content click to seek handler
  const handleTimelineContentClick = useCallback(
    (e: React.MouseEvent) => {
      // A portaled menu's click (React bubbles it here) is not a click on the tracks.
      if (!e.currentTarget.contains(e.target as Node)) return;
      // Don't seek if this was a selection box operation (its release's click is swallowed below)
      if (isSelecting) {
        return;
      }

      // Don't seek if clicking on timeline elements, but still deselect
      if ((e.target as HTMLElement).closest(".timeline-element")) {
        return;
      }

      // Don't seek if clicking on playhead
      if (playheadRef.current?.contains(e.target as Node)) {
        return;
      }

      // Clear selected elements when clicking empty timeline area
      clearSelectedElements();

      const tracksViewport = tracksViewportRef.current;
      if (!tracksViewport) return;
      const rect = tracksViewport.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const rawTime = Math.max(
        0,
        Math.min(
          duration,
          (mouseX + tracksViewport.scrollLeft) /
            (TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel),
        ),
      );

      // Use frame snapping for timeline clicking
      seek(snapTimeToFrame(rawTime, TIMELINE_FPS));
    },
    [duration, zoomLevel, seek, clearSelectedElements, isSelecting],
  );

  // The playhead runs on the edited reel (its duration is the reel's length),
  // or on the clock the caller names (the rendered clip in the result view).
  const clockDuration = playbackDuration ?? reelDuration;
  useEffect(() => {
    setDuration(clockDuration);
    if (usePlaybackStore.getState().currentTime > clockDuration) {
      usePlaybackStore.getState().seek(clockDuration);
    }
  }, [clockDuration, setDuration]);

  // --- Scroll synchronization effect ---
  useEffect(() => {
    const rulerViewport = rulerViewportRef.current;
    const tracksViewport = tracksViewportRef.current;
    const trackLabelsViewport = trackLabelsViewportRef.current;

    if (!rulerViewport || !tracksViewport) return;

    // Horizontal scroll synchronization between ruler and tracks
    const handleRulerScroll = () => {
      const now = Date.now();
      if (isUpdatingRef.current || now - lastRulerSync.current < 16) return;
      lastRulerSync.current = now;
      isUpdatingRef.current = true;
      tracksViewport.scrollLeft = rulerViewport.scrollLeft;
      isUpdatingRef.current = false;
    };
    const handleTracksScroll = () => {
      const now = Date.now();
      if (isUpdatingRef.current || now - lastTracksSync.current < 16) return;
      lastTracksSync.current = now;
      isUpdatingRef.current = true;
      rulerViewport.scrollLeft = tracksViewport.scrollLeft;
      isUpdatingRef.current = false;
    };
    // Vertical scroll synchronization between track labels and tracks content
    const handleTrackLabelsScroll = () => {
      const now = Date.now();
      if (
        !trackLabelsViewport ||
        isUpdatingRef.current ||
        now - lastVerticalSync.current < 16
      )
        return;
      lastVerticalSync.current = now;
      isUpdatingRef.current = true;
      tracksViewport.scrollTop = trackLabelsViewport.scrollTop;
      isUpdatingRef.current = false;
    };
    const handleTracksVerticalScroll = () => {
      const now = Date.now();
      if (
        !trackLabelsViewport ||
        isUpdatingRef.current ||
        now - lastVerticalSync.current < 16
      )
        return;
      lastVerticalSync.current = now;
      isUpdatingRef.current = true;
      trackLabelsViewport.scrollTop = tracksViewport.scrollTop;
      isUpdatingRef.current = false;
    };

    // The playhead sits outside the scrolling content: it follows the tracks' offset.
    const handleTracksView = () =>
      setTracksView({
        scrollLeft: tracksViewport.scrollLeft,
        width: tracksViewport.clientWidth,
      });
    const resizeObserver =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(handleTracksView);

    rulerViewport.addEventListener("scroll", handleRulerScroll);
    tracksViewport.addEventListener("scroll", handleTracksScroll);
    tracksViewport.addEventListener("scroll", handleTracksVerticalScroll);
    tracksViewport.addEventListener("scroll", handleTracksView);
    trackLabelsViewport?.addEventListener("scroll", handleTrackLabelsScroll);
    resizeObserver?.observe(tracksViewport);

    return () => {
      rulerViewport.removeEventListener("scroll", handleRulerScroll);
      tracksViewport.removeEventListener("scroll", handleTracksScroll);
      tracksViewport.removeEventListener("scroll", handleTracksVerticalScroll);
      tracksViewport.removeEventListener("scroll", handleTracksView);
      trackLabelsViewport?.removeEventListener(
        "scroll",
        handleTrackLabelsScroll,
      );
      resizeObserver?.disconnect();
    };
  }, []);

  // Action handlers for toolbar
  const splitBlocked = readOnly ?? (splitGate.ok ? null : splitGate.reason);
  const split = (mode: SplitMode) => () =>
    toastRefusal(splitAtPlayhead(mode, splitGate));
  const zoomBy = (factor: number) =>
    setZoomLevel((z) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z * factor)));

  const interval = getTimeInterval(zoomLevel);
  const markerCount = Math.ceil(duration / interval) + 1;

  return (
    <div
      data-timeline
      className={cn(
        "relative flex h-full flex-col overflow-hidden rounded-sm bg-background transition-colors duration-200",
        className,
      )}
      onMouseEnter={() => setIsInTimeline(true)}
      onMouseLeave={() => setIsInTimeline(false)}
      // The click that ends an edge resize or a selection box neither deselects nor seeks.
      // Only a click on the timeline itself: React bubbles a portaled menu's click through here.
      onClickCapture={(e) => {
        if (e.currentTarget.contains(e.target as Node) && takeSuppressedClick())
          e.stopPropagation();
      }}
    >
      {/* Toolbar */}
      <div
        role="toolbar"
        aria-label="Timeline"
        className="flex items-center gap-1 border-b px-2 py-1"
      >
        <TooltipProvider delay={500}>
          <ToolbarButton
            label={isPlaying ? "Pause" : "Play"}
            hint={isPlaying ? "Pause (Space)" : "Play (Space)"}
            onClick={toggle}
          >
            {isPlaying ? <Pause /> : <Play />}
          </ToolbarButton>
          <div className="mx-1 h-6 w-px bg-border" />
          {/* Time Display */}
          <div
            className="px-2 text-center font-mono text-xs text-muted-foreground"
            style={{ minWidth: "18ch" }}
          >
            {currentTime.toFixed(1)}s / {duration.toFixed(1)}s
          </div>
          <div className="mx-1 h-6 w-px bg-border" />
          <ToolbarButton
            label="Split"
            hint="Split the segment at the playhead (S)"
            blockedReason={splitBlocked}
            disabled={splitBlocked !== null}
            onClick={split("split")}
          >
            <Scissors />
          </ToolbarButton>
          <ToolbarButton
            label="Split keep left"
            hint="Split at the playhead and keep the left part"
            blockedReason={splitBlocked}
            disabled={splitBlocked !== null}
            onClick={split("keep-left")}
          >
            <ArrowLeftToLine />
          </ToolbarButton>
          <ToolbarButton
            label="Split keep right"
            hint="Split at the playhead and keep the right part"
            blockedReason={splitBlocked}
            disabled={splitBlocked !== null}
            onClick={split("keep-right")}
          >
            <ArrowRightToLine />
          </ToolbarButton>
          <ToolbarButton
            label="Delete"
            hint="Delete the selection (Delete)"
            disabled={readOnly !== null || selectedElements.length === 0}
            onClick={() => toastRefusal(deleteSelection())}
          >
            <Trash2 />
          </ToolbarButton>
          <div className="mx-1 h-6 w-px bg-border" />
          <ToolbarButton
            label="Undo"
            hint="Undo (Cmd/Ctrl+Z)"
            disabled={!canUndo}
            onClick={() => useTimelineStore.getState().undo()}
          >
            <Undo2 />
          </ToolbarButton>
          <ToolbarButton
            label="Redo"
            hint="Redo (Shift+Cmd/Ctrl+Z)"
            disabled={!canRedo}
            onClick={() => useTimelineStore.getState().redo()}
          >
            <Redo2 />
          </ToolbarButton>
          <div className="mx-1 h-6 w-px bg-border" />
          <ToolbarButton
            label="Zoom out"
            hint="Zoom out (Cmd/Ctrl+scroll)"
            disabled={zoomLevel <= ZOOM_MIN}
            onClick={() => zoomBy(0.5)}
          >
            <ZoomOut />
          </ToolbarButton>
          <ToolbarButton
            label="Zoom in"
            hint="Zoom in (Cmd/Ctrl+scroll)"
            disabled={zoomLevel >= ZOOM_MAX}
            onClick={() => zoomBy(2)}
          >
            <ZoomIn />
          </ToolbarButton>
        </TooltipProvider>
      </div>
      {readOnly ? (
        <p
          role="note"
          className="border-b px-3 py-1 text-xs text-muted-foreground"
        >
          {readOnly}
        </p>
      ) : null}

      {/* Timeline Container */}
      <div
        className="relative flex flex-1 flex-col overflow-hidden"
        ref={timelineRef}
      >
        <TimelinePlayhead
          position={playheadPosition}
          duration={duration}
          zoomLevel={zoomLevel}
          trackLabelsWidth={tracks.length > 0 ? TRACK_LABELS_PX : 0}
          scrollLeft={tracksView.scrollLeft}
          viewportWidth={tracksView.width}
          playheadRef={playheadRef}
          onMouseDown={handlePlayheadMouseDown}
          onKeyDown={handlePlayheadKeyDown}
        />
        {/* Timeline Header with Ruler */}
        <div className="sticky top-0 z-10 flex bg-background">
          {/* Track Labels Header */}
          <div
            className="flex shrink-0 items-center justify-between border-r bg-muted/30 px-3 py-2"
            style={{ width: TRACK_LABELS_PX }}
          />

          {/* Timeline Ruler */}
          <div
            className="relative h-4 flex-1 overflow-hidden"
            onWheel={handleWheel}
            data-ruler-area
          >
            <ScrollArea
              className="w-full"
              ref={rulerScrollRef}
              viewportRef={rulerViewportRef}
            >
              <div
                ref={rulerRef}
                data-ruler
                aria-hidden="true"
                className="relative h-4 min-w-full cursor-default select-none"
                style={{
                  width: `${contentWidth}px`,
                }}
                onMouseDown={handleRulerMouseDown}
              >
                {/* Time markers */}
                {Array.from({ length: markerCount }, (_, i) => {
                  const time = i * interval;
                  if (time > duration) return null;

                  const isMainMarker =
                    time % (interval >= 1 ? Math.max(1, interval) : 1) === 0;

                  return (
                    <div
                      key={i}
                      className={`absolute top-0 bottom-0 ${
                        isMainMarker
                          ? "border-l border-muted-foreground/40"
                          : "border-l border-muted-foreground/20"
                      }`}
                      style={{
                        left: `${time * TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel}px`,
                      }}
                    >
                      <span
                        className={`absolute top-1 left-1 text-[0.6rem] ${
                          isMainMarker
                            ? "font-medium text-muted-foreground"
                            : "text-muted-foreground/70"
                        }`}
                      >
                        {formatMark(time, interval)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </ScrollArea>
          </div>
        </div>

        {/* Tracks Area */}
        <div className="flex flex-1 overflow-hidden">
          {/* Track Labels */}
          {tracks.length > 0 && (
            <div
              className="shrink-0 overflow-y-auto border-r bg-muted/30"
              style={{ width: TRACK_LABELS_PX }}
              data-track-labels
            >
              <ScrollArea
                className="h-full w-full"
                viewportRef={trackLabelsViewportRef}
              >
                <div className="flex flex-col gap-1">
                  {tracks.map((track) => (
                    <TrackLabel key={track.id} track={track} />
                  ))}
                </div>
              </ScrollArea>
            </div>
          )}

          {/* Timeline Tracks Content */}
          <div
            className="relative flex-1 overflow-hidden"
            onWheel={handleWheel}
            onMouseDown={handleSelectionMouseDown}
            onClick={handleTimelineContentClick}
            ref={tracksContainerRef}
            data-tracks-area
          >
            <SelectionBox
              startPos={selectionBox?.startPos || null}
              currentPos={selectionBox?.currentPos || null}
              containerRef={tracksContainerRef as React.RefObject<HTMLElement>}
              isActive={selectionBox?.isActive || false}
            />
            <ScrollArea
              className="h-full w-full"
              scrollbars="both"
              ref={tracksScrollRef}
              viewportRef={tracksViewportRef}
            >
              <div
                className="relative min-w-full flex-1"
                data-timeline-content
                style={{
                  height: `${Math.max(200, Math.min(800, getTotalTracksHeight(tracks)))}px`,
                  width: `${contentWidth}px`,
                }}
              >
                {tracks.map((track, index) => (
                  <div
                    key={track.id}
                    className="absolute right-0 left-0 border-b border-muted/30 py-[0.05rem]"
                    style={{
                      top: `${getCumulativeHeightBefore(tracks, index)}px`,
                      height: `${getTrackHeight(track.type)}px`,
                    }}
                  >
                    <TimelineTrackContent
                      track={track}
                      zoomLevel={zoomLevel}
                      splitGate={splitGate}
                    />
                  </div>
                ))}
              </div>
            </ScrollArea>
          </div>
        </div>
      </div>
    </div>
  );
}

function TrackIcon({ track }: { track: EditorTrack }) {
  const className = "h-4 w-4 shrink-0 text-muted-foreground";
  switch (track.role) {
    case "segments":
      return <Video className={className} />;
    case "captions":
      return <Captions className={className} />;
    case "overlays":
      return <Type className={className} />;
    case "voiceovers":
      return <Mic className={className} />;
  }
}

function TrackLabel({ track }: { track: EditorTrack }) {
  const readOnly = useTimelineStore((s) => s.readOnly !== null);
  return (
    <div
      className="group flex items-center gap-2 border-b border-muted/30 bg-foreground/5 px-3"
      style={{ height: `${getTrackHeight(track.type)}px` }}
    >
      <TrackIcon track={track} />
      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
        {track.name}
      </span>
      {track.role === "segments" && (
        <>
          {track.muted && (
            <span className="shrink-0 text-xs font-semibold text-red-500">
              Muted
            </span>
          )}
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label="Mute source audio"
            aria-pressed={track.muted === true}
            disabled={readOnly}
            onClick={() =>
              toastRefusal(
                useTimelineStore.getState().toggleTrackMute(track.id),
              )
            }
          >
            {track.muted ? <VolumeX /> : <Volume2 />}
          </Button>
        </>
      )}
    </div>
  );
}
