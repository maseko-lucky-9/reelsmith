// Adapted from OpenCut (MIT) apps/web/src/components/editor/properties-panel/index.tsx @ 3037a7e
// Local changes: no project or media store, no aspect ratio, FPS select, media or audio properties
// (schema v2 has none of them; spec 017 research R9): the selected captions and text overlays get
// their text properties; nothing selected, a segment or a voice-over gets an empty state saying
// where it is edited. Base UI ScrollArea. Licence: ../../../LICENSE-OpenCut.
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type {
  CaptionElement,
  EditorElement,
  OverlayElement,
} from "@/editor/types";
import { useTimelineStore } from "@/editor/opencut/stores/timeline-store";
import { TextProperties } from "./text-properties";

const EMPTY = {
  nothing: "Select a caption or a text overlay on the timeline to edit it.",
  segments:
    "A video segment is trimmed on the timeline: drag its edges, or split it at the playhead.",
  voiceovers:
    "A voice-over is moved on the timeline; its audio is made in the Voice-over panel.",
} as const;

const isText = (
  element: EditorElement,
): element is CaptionElement | OverlayElement =>
  element.role === "captions" || element.role === "overlays";

export function PropertiesPanel({ className }: { className?: string }) {
  const selectedElements = useTimelineStore((s) => s.selectedElements);
  const tracks = useTimelineStore((s) => s._tracks);

  const selected = selectedElements.flatMap(({ trackId, elementId }) => {
    const track = tracks.find((t) => t.id === trackId);
    const element = track?.elements.find((e) => e.id === elementId);
    return track && element ? [{ trackId, element }] : [];
  });
  const editable = selected.filter(
    (s): s is { trackId: string; element: CaptionElement | OverlayElement } =>
      isText(s.element),
  );
  const other = selected.find((s) => !isText(s.element))?.element.role;
  const emptyText =
    other === "segments" || other === "voiceovers"
      ? EMPTY[other]
      : EMPTY.nothing;

  return (
    <ScrollArea className={cn("h-full rounded-sm", className)}>
      <div aria-label="Properties" role="region">
        {editable.length > 0 ? (
          editable.map(({ trackId, element }) => (
            <TextProperties
              key={element.id}
              element={element}
              trackId={trackId}
            />
          ))
        ) : (
          <p className="p-5 text-xs text-muted-foreground">{emptyText}</p>
        )}
      </div>
    </ScrollArea>
  );
}
