// Adapted from OpenCut (MIT) apps/web/src/components/editor/timeline-element.tsx @ 3037a7e
// Local changes: Radix context menu and dropdown to the Base UI wrappers (components/ui); no media
// store (no thumbnails, no images); a voice-over is a plain bar (no wavesurfer AudioWaveform); menu
// items only where they apply: Split, Split keep left, Split keep right on a segment (gated by the
// split gate, whose reason the menu shows; they act on this segment at the playhead) and Delete on
// every item; no duplicate, separate audio, replace clip or freeze frame. Exact widths (no 80 px
// minimum: short captions and segments would overlap their neighbours). Resize handles only where
// the resize hook allows them, drawn from its live preview. Keyboard: the item is a toggle button
// (Enter/Space select, Shift/Cmd/Ctrl add); right click selects it without starting a drag; the
// selected item has a "More actions" menu (the context menu's items). Tailwind 4 renames (R14).
// Licence: ../../LICENSE-OpenCut.
import type React from "react";
import {
  ArrowLeftToLine,
  ArrowRightToLine,
  MoreVertical,
  Scissors,
  Trash2,
} from "lucide-react";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  Menu,
  MenuContent,
  MenuGroup,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
} from "@/components/ui/menu";
import type { SplitGate } from "@/editor/renderedCut";
import type { EditorElement, EditorTrack } from "@/editor/types";
import {
  getTrackElementClasses,
  TIMELINE_CONSTANTS,
} from "@/editor/opencut/constants/timeline-constants";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import {
  SPLIT_OPEN,
  splitElementAt,
  toastRefusal,
  type SplitMode,
} from "@/editor/opencut/hooks/use-playback-controls";
import { useTimelineElementResize } from "@/editor/opencut/hooks/use-timeline-element-resize";

interface TimelineElementProps {
  element: EditorElement;
  track: EditorTrack;
  zoomLevel: number;
  isSelected: boolean;
  splitGate?: SplitGate;
  onElementMouseDown: (e: React.MouseEvent, element: EditorElement) => void;
  onElementClick: (e: React.MouseEvent, element: EditorElement) => void;
  onElementSelect: (element: EditorElement, multi: boolean) => void;
}

const seconds = (n: number) => `${Number(n.toFixed(3))}`;

/** What the element is called to assistive tech (and in tests). */
function labelOf(element: EditorElement): string {
  switch (element.role) {
    case "segments":
      return `${element.name}, source ${seconds(element.trimStart)} to ${seconds(element.duration - element.trimEnd)} s`;
    case "captions":
      return `Caption: ${element.content}`;
    case "overlays":
      return `Text: ${element.content}`;
    case "voiceovers":
      return `Voice-over: ${element.mediaId}`;
  }
}

interface Action {
  id: string;
  label: string;
  icon: React.ReactNode;
  disabled: boolean;
  destructive?: boolean;
  run: () => void;
}

/** The items' actions; split only on a segment. */
function actionsOf(
  element: EditorElement,
  track: EditorTrack,
  gate: SplitGate,
  readOnly: boolean,
): { split: Action[]; remove: Action } {
  const split = (mode: SplitMode) => () =>
    toastRefusal(splitElementAt(mode, track.id, element.id, gate));
  const splitBlocked = readOnly || !gate.ok;
  return {
    split:
      element.role === "segments"
        ? [
            {
              id: "split",
              label: "Split",
              icon: <Scissors />,
              disabled: splitBlocked,
              run: split("split"),
            },
            {
              id: "keep-left",
              label: "Split keep left",
              icon: <ArrowLeftToLine />,
              disabled: splitBlocked,
              run: split("keep-left"),
            },
            {
              id: "keep-right",
              label: "Split keep right",
              icon: <ArrowRightToLine />,
              disabled: splitBlocked,
              run: split("keep-right"),
            },
          ]
        : [],
    remove: {
      id: "delete",
      label: "Delete",
      icon: <Trash2 />,
      disabled: readOnly,
      destructive: true,
      run: () =>
        toastRefusal(
          useTimelineStore
            .getState()
            .removeElementFromTrack(track.id, element.id),
        ),
    },
  };
}

export function TimelineElement({
  element,
  track,
  zoomLevel,
  isSelected,
  splitGate = SPLIT_OPEN,
  onElementMouseDown,
  onElementClick,
  onElementSelect,
}: TimelineElementProps) {
  const dragState = useTimelineStore((s) => s.dragState);
  const readOnlyReason = useTimelineStore((s) => s.readOnly);
  const { handles, preview, handleResizeStart } = useTimelineElementResize({
    element,
    track,
    zoomLevel,
  });

  const pixelsPerSecond = TIMELINE_CONSTANTS.PIXELS_PER_SECOND * zoomLevel;
  const effectiveDuration =
    preview?.visibleDuration ??
    element.duration - element.trimStart - element.trimEnd;
  const elementWidth = Math.max(2, effectiveDuration * pixelsPerSecond);

  // Use real-time position during drag, otherwise use stored position
  const isBeingDragged =
    dragState.isDragging && dragState.elementId === element.id;
  const elementStartTime = isBeingDragged
    ? dragState.currentTime
    : (preview?.startTime ?? element.startTime);
  const elementLeft = elementStartTime * pixelsPerSecond;

  const label = labelOf(element);
  const { split, remove } = actionsOf(
    element,
    track,
    splitGate,
    readOnlyReason !== null,
  );
  const blockedReason =
    readOnlyReason ?? (splitGate.ok ? null : splitGate.reason);
  const draggable =
    readOnlyReason === null &&
    (element.role === "overlays" || element.role === "voiceovers");

  const renderElementContent = () => {
    if (element.type === "text") {
      return (
        <div className="flex h-full w-full items-center justify-start pl-2">
          <span className="truncate text-xs text-white/90">
            {element.content}
          </span>
        </div>
      );
    }
    if (element.role === "voiceovers") {
      // ponytail: a plain bar, no waveform; add waveforms (wavesurfer.js or
      // server-computed peaks) when voice-over editing needs them (T160).
      return (
        <span className="truncate pl-2 text-xs text-foreground/80">
          {element.mediaId}
        </span>
      );
    }
    return (
      <span className="truncate pl-2 text-xs text-foreground/80">
        {element.name} · {seconds(element.trimStart)}–
        {seconds(element.duration - element.trimEnd)} s
      </span>
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    e.stopPropagation();
    onElementSelect(element, e.shiftKey || e.metaKey || e.ctrlKey);
  };

  return (
    <ContextMenu>
      <ContextMenuTrigger
        className={`timeline-element absolute top-0 h-full select-none ${
          isBeingDragged ? "z-50" : "z-10"
        }`}
        style={{
          left: `${elementLeft}px`,
          width: `${elementWidth}px`,
        }}
        data-element-id={element.id}
        data-track-id={track.id}
        data-role={element.role}
      >
        <div
          role="button"
          tabIndex={0}
          aria-pressed={isSelected}
          aria-label={label}
          className={`relative h-full overflow-hidden rounded-[0.15rem] outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            draggable ? "cursor-grab" : "cursor-pointer"
          } ${getTrackElementClasses(track.type)} ${
            isSelected
              ? "border-t-[0.5px] border-b-[0.5px] border-foreground ring-1 ring-foreground"
              : ""
          }`}
          onClick={(e) => onElementClick(e, element)}
          onMouseDown={(e) => onElementMouseDown(e, element)}
          onKeyDown={handleKeyDown}
          onContextMenu={() => {
            if (!isSelected) onElementSelect(element, false);
          }}
        >
          <div className="absolute inset-0 flex h-full items-center">
            {renderElementContent()}
          </div>

          {isSelected && handles.left && (
            <div
              aria-hidden="true"
              data-resize-handle="left"
              className="absolute top-0 bottom-0 left-0 z-50 w-1 cursor-w-resize bg-foreground"
              onMouseDown={(e) => handleResizeStart(e, element.id, "left")}
            />
          )}
          {isSelected && handles.right && (
            <div
              aria-hidden="true"
              data-resize-handle="right"
              className="absolute top-0 right-0 bottom-0 z-50 w-1 cursor-e-resize bg-foreground"
              onMouseDown={(e) => handleResizeStart(e, element.id, "right")}
            />
          )}
        </div>
        {isSelected && (
          <Menu>
            <MenuTrigger
              aria-label={`More actions for ${label}`}
              className="absolute top-1/2 right-2 z-50 -translate-y-1/2 rounded-sm bg-background/80 p-0.5 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onMouseDown={(e: React.MouseEvent) => e.stopPropagation()}
            >
              <MoreVertical className="size-3" />
            </MenuTrigger>
            <MenuContent>
              {split.length > 0 && (
                <MenuGroup>
                  {blockedReason && (
                    <MenuLabel className="max-w-64">{blockedReason}</MenuLabel>
                  )}
                  {split.map((action) => (
                    <MenuItem
                      key={action.id}
                      disabled={action.disabled}
                      onClick={action.run}
                    >
                      {action.icon}
                      {action.label}
                    </MenuItem>
                  ))}
                </MenuGroup>
              )}
              {split.length > 0 && <MenuSeparator />}
              <MenuItem
                variant="destructive"
                disabled={remove.disabled}
                onClick={remove.run}
              >
                {remove.icon}
                {remove.label}
              </MenuItem>
            </MenuContent>
          </Menu>
        )}
      </ContextMenuTrigger>
      <ContextMenuContent>
        {split.length > 0 && (
          <ContextMenuGroup>
            {blockedReason && (
              <ContextMenuLabel className="max-w-64">
                {blockedReason}
              </ContextMenuLabel>
            )}
            {split.map((action) => (
              <ContextMenuItem
                key={action.id}
                disabled={action.disabled}
                onClick={action.run}
              >
                {action.icon}
                {action.label}
              </ContextMenuItem>
            ))}
          </ContextMenuGroup>
        )}
        {split.length > 0 && <ContextMenuSeparator />}
        <ContextMenuItem
          variant="destructive"
          disabled={remove.disabled}
          onClick={remove.run}
        >
          {remove.icon}
          {remove.label}
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  );
}
