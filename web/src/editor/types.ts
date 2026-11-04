/**
 * Reelsmith's extensions of OpenCut's timeline types (spec 017 data-model §2).
 *
 * The copied OpenCut types (`opencut/types/timeline.ts`) are never edited;
 * every Reelsmith field is added here by intersection. An element carries
 * the schema v2 item it came from (`origin`) and a track the v2 track minus
 * its items (`originTrack`), so `toTimeline` can write back every field the
 * editor did not change exactly as loaded (ADR-015 §4).
 */
import type {
  TimelineItem,
  TimelinePayload,
  TimelineTrack as SchemaTrack,
} from "@/api/client";
import type { SourceWindow } from "@/lib/timelineEdit";
import type {
  MediaElement,
  TextElement,
  TimelineTrack,
} from "@/editor/opencut/types/timeline";

/** What a track (and every element on it) holds; one per schema v2 track kind. */
export type TrackRole = "segments" | "captions" | "overlays" | "voiceovers";

/** A v2 video item: a window of the source, on the one `segments` track (`isMain`). */
export type SegmentElement = MediaElement & {
  role: "segments";
  origin: TimelineItem;
};
/** A v2 caption item (reel clock); `words`, `style` and unknown keys stay in `origin`. */
export type CaptionElement = TextElement & {
  role: "captions";
  origin: TimelineItem;
};
/** A v2 text-overlay item (reel clock); position in OpenCut pixels from the canvas centre. */
export type OverlayElement = TextElement & {
  role: "overlays";
  origin: TimelineItem;
};
/** A v2 audio item (reel clock); `mediaId` is the server's bare file name, `gain` stays in `origin`. */
export type VoiceoverElement = MediaElement & {
  role: "voiceovers";
  origin: TimelineItem;
};

export type EditorElement =
  SegmentElement | CaptionElement | OverlayElement | VoiceoverElement;

/** A schema v2 track without its items (its `kind` and any unknown keys). */
export type SchemaTrackOrigin = Omit<SchemaTrack, "items">;

export type EditorTrack = Omit<TimelineTrack, "elements"> & {
  elements: EditorElement[];
  role: TrackRole;
  /** The loaded v2 track minus its items; null for a track the editor created (written once it has items). */
  originTrack: SchemaTrackOrigin | null;
};

/** The editor's working form of one edit; also an undo/redo snapshot (history entries are whole states). */
export interface EditorState {
  tracks: EditorTrack[];
  /** A caption preset key, null for the default look stated, undefined when the key is absent. */
  captionStyle: string | null | undefined;
  /** The timeline's top-level keys as loaded (`schema_version`, `caption_style`, `mute_source`, unknown keys). */
  timelineOrigin: Omit<TimelinePayload, "tracks">;
}

export type EditorSnapshot = EditorState;

export interface AdapterContext {
  /** The clip's window in its source: converts a v1 edit (016 `upgradeTimeline`). */
  sourceWindow: SourceWindow | null;
  /** The source video's length; null: the furthest segment end bounds an extension. */
  sourceDuration: number | null;
}

export type AdapterConversion = "none" | "v1" | "merged-video-tracks";

export type AdapterResult =
  | { ok: true; state: EditorState; converted: AdapterConversion }
  | { ok: false; reason: string };
