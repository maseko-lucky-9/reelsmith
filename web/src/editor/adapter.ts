/**
 * Timeline schema v2 (ADR-014) <-> the editor's OpenCut-shaped state
 * (spec 017 FR-004, FR-005; contracts/timeline-adapter.md).
 *
 * The only code that knows both shapes. The round trip is the contract:
 * `toTimeline(fromTimeline(t).state)` deep-equals every schema v2 `t` with at
 * most one video track. Each element keeps the v2 item it came from
 * (`origin`); `toTimeline` writes `{...origin, ...changed}`, where `changed`
 * holds only the fields whose editor value differs from the value
 * `fromTimeline` derived from `origin`, rounded (times to ms, positions to
 * four decimals, font sizes to an integer in [6, 400]). Recomputing instead
 * would turn `{start: 0.1, end: 0.3}` into `end: 0.30000000000000004` and
 * dirty every opened clip (research R6).
 *
 * A segment is an OpenCut media element spanning the whole source, trimmed
 * to its window (`duration` = source duration, `trimStart` = start,
 * `trimEnd` = duration - end), placed back to back on the reel (R5).
 */
import type {
  TimelineItem,
  TimelinePayload,
  TimelineTrack as SchemaTrack,
  TimelineTrackKind,
} from "@/api/client";
import { isV2, upgradeTimeline, type SourceWindow } from "@/lib/timelineEdit";
import type { TrackType } from "@/editor/opencut/types/timeline";
import type {
  AdapterContext,
  AdapterConversion,
  AdapterResult,
  CaptionElement,
  EditorElement,
  EditorState,
  EditorTrack,
  OverlayElement,
  SchemaTrackOrigin,
  SegmentElement,
  TrackRole,
  VoiceoverElement,
} from "./types";

/** The canvas OpenCut's overlay pixels are measured on (the reel's 1080x1920). */
export const OVERLAY_CANVAS = { width: 1080, height: 1920 } as const;

/** Schema v2 overlay defaults (data-model §1): `font_size` 36, `x` 0.5, `y` 0.1, `color` #ffffff. */
export const OVERLAY_DEFAULTS = {
  fontSize: 36,
  x: 0.5,
  y: 0.1,
  color: "#ffffff",
} as const;

/** Overlay `font_size`: an integer in [6, 400] (server FONT_SIZE_RANGE). */
export const FONT_SIZE_RANGE = [6, 400] as const;

/**
 * What the editor opens when the clip has no edit (404): the 016 editor's v1
 * placeholder, a zero-length video item that `upgradeTimeline` turns into
 * the clip's whole window.
 */
export const NO_EDIT_TIMELINE: TimelinePayload = {
  tracks: [
    { kind: "video", items: [{ start: 0, end: 0, src: "main" }] },
    { kind: "caption", items: [] },
    { kind: "text-overlay", items: [] },
  ],
};

export const ROLE_OF_KIND: Record<TimelineTrackKind, TrackRole> = {
  video: "segments",
  caption: "captions",
  "text-overlay": "overlays",
  audio: "voiceovers",
};

export const KIND_OF_ROLE: Record<TrackRole, TimelineTrackKind> = {
  segments: "video",
  captions: "caption",
  overlays: "text-overlay",
  voiceovers: "audio",
};

export const TRACK_TYPE_OF_ROLE: Record<TrackRole, TrackType> = {
  segments: "media",
  captions: "text",
  overlays: "text",
  voiceovers: "audio",
};

/** Track labels (spec 017 T149: Video / Captions / Text / Voice-over). */
export const TRACK_NAME_OF_ROLE: Record<TrackRole, string> = {
  segments: "Video",
  captions: "Captions",
  overlays: "Text",
  voiceovers: "Voice-over",
};

/** OpenCut's defaults for a new text element (its timeline text drop); v2 fields override them. */
const TEXT_DEFAULTS = {
  fontSize: 48,
  fontFamily: "Arial",
  color: "#ffffff",
  backgroundColor: "transparent",
  textAlign: "center",
  fontWeight: "normal",
  fontStyle: "normal",
  textDecoration: "none",
  x: 0,
  y: 0,
  rotation: 0,
  opacity: 1,
} as const;

const round3 = (n: number) => Math.round(n * 1000) / 1000;
const round4 = (n: number) => Math.round(n * 10000) / 10000;

export function clampFontSize(n: number): number {
  return Math.min(
    FONT_SIZE_RANGE[1],
    Math.max(FONT_SIZE_RANGE[0], Math.round(n)),
  );
}

// ── Values derived from an origin item ──────────────────────────────────────
// fromTimeline sets the editor fields with these, and toTimeline compares
// against them: the same expression on the same input gives the same float,
// so an unchanged field is recognised exactly.

const startOf = (origin: TimelineItem) => Number(origin.start);
const endOf = (origin: TimelineItem) => Number(origin.end);
const reelLengthOf = (origin: TimelineItem) => endOf(origin) - startOf(origin);
const textOf = (origin: TimelineItem) =>
  origin.text == null ? "" : String(origin.text);
const finiteOr = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;
const overlayXOf = (origin: TimelineItem) =>
  (finiteOr(origin.x, OVERLAY_DEFAULTS.x) - 0.5) * OVERLAY_CANVAS.width;
const overlayYOf = (origin: TimelineItem) =>
  (finiteOr(origin.y, OVERLAY_DEFAULTS.y) - 0.5) * OVERLAY_CANVAS.height;
const fontSizeOf = (origin: TimelineItem) =>
  finiteOr(origin.font_size, OVERLAY_DEFAULTS.fontSize);
const colorOf = (origin: TimelineItem) =>
  typeof origin.color === "string" ? origin.color : OVERLAY_DEFAULTS.color;

// ── fromTimeline ────────────────────────────────────────────────────────────

/** Why a v1 edit cannot be converted (it then opens read-only). */
function v1Refusal(
  timeline: TimelinePayload,
  window: SourceWindow | null,
): string {
  if (!window || !(window.end > window.start) || window.start < 0) {
    return "This clip's place in its source video is unknown, so its saved edit (schema v1) cannot be converted to schema v2. It opens read-only.";
  }
  const video = timeline.tracks
    .filter((t) => t.kind === "video")
    .flatMap((t) => t.items);
  if (video.some((it) => it.src != null && it.src !== "main")) {
    return "The saved edit (schema v1) plays another file than the clip, so it cannot be converted to schema v2. It opens read-only.";
  }
  return "The saved edit (schema v1) cannot be converted to schema v2. It opens read-only.";
}

function originTrackOf(track: SchemaTrack): SchemaTrackOrigin {
  const rest: Partial<SchemaTrack> = { ...track };
  delete rest.items;
  return rest as SchemaTrackOrigin;
}

function withoutTracks(
  timeline: TimelinePayload,
): Omit<TimelinePayload, "tracks"> {
  const rest: Partial<TimelinePayload> = { ...timeline };
  delete rest.tracks;
  return rest;
}

export function makeTrack(
  role: TrackRole,
  originTrack: SchemaTrackOrigin | null,
  elements: EditorElement[],
  muted = false,
): EditorTrack {
  return {
    id: crypto.randomUUID(),
    name: TRACK_NAME_OF_ROLE[role],
    type: TRACK_TYPE_OF_ROLE[role],
    elements,
    muted: role === "segments" ? muted : false,
    isMain: role === "segments",
    role,
    originTrack,
  };
}

function segmentElements(
  items: TimelineItem[],
  sourceDuration: number,
): SegmentElement[] {
  let reel = 0;
  return items.map((origin, i) => {
    const start = startOf(origin);
    const end = endOf(origin);
    const element: SegmentElement = {
      id: crypto.randomUUID(),
      name: `Segment ${i + 1}`,
      type: "media",
      mediaId: typeof origin.src === "string" ? origin.src : "main",
      duration: sourceDuration,
      startTime: reel,
      trimStart: start,
      trimEnd: sourceDuration - end,
      role: "segments",
      origin,
    };
    reel += Math.max(0, end - start);
    return element;
  });
}

function captionElement(origin: TimelineItem, i: number): CaptionElement {
  return {
    ...TEXT_DEFAULTS,
    id: crypto.randomUUID(),
    name: `Caption ${i + 1}`,
    type: "text",
    content: textOf(origin),
    startTime: startOf(origin),
    duration: reelLengthOf(origin),
    trimStart: 0,
    trimEnd: 0,
    role: "captions",
    origin,
  };
}

function overlayElement(origin: TimelineItem, i: number): OverlayElement {
  return {
    ...TEXT_DEFAULTS,
    id: crypto.randomUUID(),
    name: `Text ${i + 1}`,
    type: "text",
    content: textOf(origin),
    fontSize: fontSizeOf(origin),
    color: colorOf(origin),
    x: overlayXOf(origin),
    y: overlayYOf(origin),
    startTime: startOf(origin),
    duration: reelLengthOf(origin),
    trimStart: 0,
    trimEnd: 0,
    role: "overlays",
    origin,
  };
}

function voiceoverElement(origin: TimelineItem, i: number): VoiceoverElement {
  return {
    id: crypto.randomUUID(),
    name: `Voice-over ${i + 1}`,
    type: "media",
    mediaId: typeof origin.src === "string" ? origin.src : "",
    startTime: startOf(origin),
    duration: reelLengthOf(origin),
    trimStart: 0,
    trimEnd: 0,
    role: "voiceovers",
    origin,
  };
}

const REEL_ELEMENT: Record<
  Exclude<TrackRole, "segments">,
  (origin: TimelineItem, i: number) => EditorElement
> = {
  captions: captionElement,
  overlays: overlayElement,
  voiceovers: voiceoverElement,
};

/**
 * The editor state of a stored edit. A v1 edit is converted through the
 * clip's window first (016 `upgradeTimeline`); several video tracks are
 * merged, in order, onto one segments track at the first one's position.
 */
export function fromTimeline(
  timeline: TimelinePayload,
  ctx: AdapterContext,
): AdapterResult {
  let v2 = timeline;
  let converted: AdapterConversion = "none";
  if (!isV2(timeline)) {
    const upgraded = upgradeTimeline(timeline, ctx.sourceWindow);
    if (!upgraded)
      return { ok: false, reason: v1Refusal(timeline, ctx.sourceWindow) };
    v2 = upgraded;
    converted = "v1";
  }
  const unknown = v2.tracks.find((t) => !(t.kind in ROLE_OF_KIND));
  if (unknown)
    return {
      ok: false,
      reason: `The edit has a track of an unknown kind (${String(unknown.kind)}).`,
    };

  const videoTracks = v2.tracks.filter((t) => t.kind === "video");
  // upgradeTimeline writes the same segments into every video track of a v1 edit: read the first.
  const segmentItems =
    converted === "v1"
      ? (videoTracks[0]?.items ?? [])
      : videoTracks.flatMap((t) => t.items);
  if (videoTracks.length > 1 && converted === "none")
    converted = "merged-video-tracks";

  // An extension is bounded by the source; until its length is known, by the furthest segment end.
  const furthest = segmentItems.map(endOf).filter(Number.isFinite);
  const sourceDuration = Math.max(ctx.sourceDuration ?? 0, ...furthest, 0);
  const muted = v2.mute_source === true;

  const tracks: EditorTrack[] = [];
  let hasSegments = false;
  for (const track of v2.tracks) {
    if (track.kind === "video") {
      if (hasSegments) continue;
      hasSegments = true;
      tracks.push(
        makeTrack(
          "segments",
          originTrackOf(track),
          segmentElements(segmentItems, sourceDuration),
          muted,
        ),
      );
      continue;
    }
    const role = ROLE_OF_KIND[track.kind] as Exclude<TrackRole, "segments">;
    tracks.push(
      makeTrack(
        role,
        originTrackOf(track),
        track.items.map(REEL_ELEMENT[role]),
      ),
    );
  }
  if (!hasSegments) tracks.unshift(makeTrack("segments", null, [], muted));

  return {
    ok: true,
    converted,
    state: {
      tracks,
      captionStyle: v2.caption_style,
      timelineOrigin: withoutTracks(v2),
    },
  };
}

// ── toTimeline ──────────────────────────────────────────────────────────────

type Changes = Record<string, unknown>;

/** The origin itself when nothing changed, else `{...origin, ...changed}`. */
function writeBack(origin: TimelineItem, changed: Changes): TimelineItem {
  return Object.keys(changed).length === 0
    ? origin
    : ({ ...origin, ...changed } as TimelineItem);
}

/** Reel-clock times: the visible span `[startTime, startTime + duration - trims]`. */
function reelTimes(el: EditorElement, changed: Changes): void {
  const { origin } = el;
  const length = el.duration - el.trimStart - el.trimEnd;
  const startChanged = !("start" in origin) || el.startTime !== startOf(origin);
  if (startChanged) changed.start = round3(el.startTime);
  if (startChanged || !("end" in origin) || length !== reelLengthOf(origin)) {
    changed.end = round3(el.startTime + length);
  }
}

function writeSegment(el: SegmentElement): TimelineItem {
  const { origin } = el;
  const changed: Changes = {};
  if (!("start" in origin) || el.trimStart !== startOf(origin))
    changed.start = round3(el.trimStart);
  if (!("end" in origin) || el.trimEnd !== el.duration - endOf(origin))
    changed.end = round3(el.duration - el.trimEnd);
  // `src` is never derived from `mediaId`: the server's value (or its absence) is kept.
  return writeBack(origin, changed);
}

function writeCaption(el: CaptionElement): TimelineItem {
  const changed: Changes = {};
  reelTimes(el, changed);
  if (el.content !== textOf(el.origin)) changed.text = el.content;
  return writeBack(el.origin, changed);
}

function writeOverlay(el: OverlayElement): TimelineItem {
  const { origin } = el;
  const changed: Changes = {};
  reelTimes(el, changed);
  if (el.content !== textOf(origin)) changed.text = el.content;
  if (el.x !== overlayXOf(origin))
    changed.x = round4(el.x / OVERLAY_CANVAS.width + 0.5);
  if (el.y !== overlayYOf(origin))
    changed.y = round4(el.y / OVERLAY_CANVAS.height + 0.5);
  if (el.fontSize !== fontSizeOf(origin))
    changed.font_size = clampFontSize(el.fontSize);
  if (el.color !== colorOf(origin)) changed.color = el.color;
  return writeBack(origin, changed);
}

function writeVoiceover(el: VoiceoverElement): TimelineItem {
  // Times only: `src` and `gain` are the server's.
  const changed: Changes = {};
  reelTimes(el, changed);
  return writeBack(el.origin, changed);
}

function writeItem(el: EditorElement): TimelineItem {
  switch (el.role) {
    case "segments":
      return writeSegment(el);
    case "captions":
      return writeCaption(el);
    case "overlays":
      return writeOverlay(el);
    case "voiceovers":
      return writeVoiceover(el);
  }
}

const byStart = (a: TimelineItem, b: TimelineItem) =>
  Number(a.start) - Number(b.start);

/** The edit as schema v2: tracks in their loaded order, unchanged values as loaded. */
export function toTimeline(state: EditorState): TimelinePayload {
  // Roles never mix: an element sitting on another role's track is written
  // to the first track of its own role (guarantee 5).
  const strays = new Map<TrackRole, EditorElement[]>();
  const firstTrackOf = new Map<TrackRole, number>();
  state.tracks.forEach((track, i) => {
    if (!firstTrackOf.has(track.role)) firstTrackOf.set(track.role, i);
    for (const el of track.elements) {
      if (el.role !== track.role)
        strays.set(el.role, [...(strays.get(el.role) ?? []), el]);
    }
  });

  const tracks: SchemaTrack[] = [];
  state.tracks.forEach((track, i) => {
    let items = track.elements
      .filter((el) => el.role === track.role)
      .map(writeItem);
    const received =
      firstTrackOf.get(track.role) === i ? strays.get(track.role) : undefined;
    if (received) {
      items = [...items, ...received.map(writeItem)].sort(byStart);
      strays.delete(track.role);
    }
    // A track the editor created is written only once it holds something.
    if (track.originTrack === null && items.length === 0) return;
    tracks.push({
      ...track.originTrack,
      kind: KIND_OF_ROLE[track.role],
      items,
    });
  });
  for (const [role, elements] of strays) {
    tracks.push({
      kind: KIND_OF_ROLE[role],
      items: elements.map(writeItem).sort(byStart),
    });
  }

  const out: TimelinePayload = {
    ...state.timelineOrigin,
    tracks,
    schema_version: 2,
  };
  if (state.captionStyle !== state.timelineOrigin.caption_style) {
    if (state.captionStyle === undefined) delete out.caption_style;
    else out.caption_style = state.captionStyle;
  }
  const muted = state.tracks.find((t) => t.role === "segments")?.muted === true;
  if (muted !== (state.timelineOrigin.mute_source === true))
    out.mute_source = muted;
  return out;
}
