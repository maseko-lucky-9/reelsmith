// Adapted from OpenCut (MIT) apps/web/src/stores/timeline-store.ts @ 3037a7e
// Local changes: storageService, the media/project/editor stores, separateAudio, replaceElementMedia,
// load/saveProjectTimeline and auto-save removed; tracks carry Reelsmith roles and v2 origins
// (web/src/editor/types.ts); history holds whole EditorSnapshots, capped at 50, and redo stays undoable;
// segment split/trim/delete go through timelineEdit (splitAt / setSegments) and the adapter; overlay and
// voice-over moves are clamped to the reel; captions take their text only; edits that break a rule
// return a reason and change nothing; new load / replaceFromTimeline / adoptTimeline / setSourceDuration
// (the preview's probed length wins over the job's) /
// toTimeline / readOnly. Licence: ../../LICENSE-OpenCut.
import { create } from "zustand";
import type { TimelinePayload } from "@/api/client";
import {
  longEnough,
  MAX_SEGMENTS,
  MIN_EDIT_SEGMENT_SECONDS,
  MIN_SEGMENT_SECONDS,
  segmentEditRefusal,
  segmentsOf,
  segmentsRefusal,
  setSegments,
  splitAt,
  splitPoint,
  type Segment,
  type SourceWindow,
} from "@/lib/timelineEdit";
import {
  clampFontSize,
  fromTimeline,
  makeTrack,
  OVERLAY_CANVAS,
  toTimeline as stateToTimeline,
} from "@/editor/adapter";
import type {
  AdapterContext,
  AdapterConversion,
  AdapterResult,
  EditorElement,
  EditorSnapshot,
  EditorState,
  EditorTrack,
  OverlayElement,
  TrackRole,
} from "@/editor/types";
import {
  sortTracksByOrder,
  type CreateTimelineElement,
  type TextElement,
} from "@/editor/opencut/types/timeline";

/** Undo steps kept (016 HISTORY_LIMIT; OpenCut's history was unbounded). */
export const HISTORY_LIMIT = 50;

/** Server limits the store enforces client-side (data-model §1). */
const MAX_TEXT_CHARS = 500;
const MAX_OVERLAY_LINES = 10;
const MAX_COLOR_CHARS = 32;
/** Words in one caption's text (server MAX_CAPTION_WORDS) and in all captions (MAX_TIMELINE_CAPTION_WORDS). */
const MAX_CAPTION_WORDS = 100;
const MAX_TIMELINE_CAPTION_WORDS = 5000;
/** Characters in all text overlays (server MAX_TIMELINE_OVERLAY_CHARS). */
const MAX_TIMELINE_OVERLAY_CHARS = 5000;
const EPS = 1e-9;

export type EditResult = { ok: true } | { ok: false; reason: string };
export type SplitResult =
  { ok: true; id: string } | { ok: false; reason: string };

type Selection = { trackId: string; elementId: string };
type TextUpdates = Partial<
  Pick<
    TextElement,
    | "content"
    | "fontSize"
    | "fontFamily"
    | "color"
    | "backgroundColor"
    | "textAlign"
    | "fontWeight"
    | "fontStyle"
    | "textDecoration"
    | "x"
    | "y"
    | "rotation"
    | "opacity"
  >
>;

const OK: EditResult = { ok: true };
const refuse = (reason: string): { ok: false; reason: string } => ({
  ok: false,
  reason,
});
const round3 = (n: number) => Math.round(n * 1000) / 1000;
/** The server's word count (Python's `str.split()`: runs of whitespace). */
const wordCount = (text: string) =>
  text.split(/\s+/).filter((w) => w.length > 0).length;
const visibleLength = (el: EditorElement) =>
  el.duration - el.trimStart - el.trimEnd;
const clamp = (n: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, n));

const REFUSALS = {
  splitRole:
    "Only video segments can be split; captions, text and voice-overs are not split.",
  splitEdge: `Split at least ${MIN_EDIT_SEGMENT_SECONDS} s inside a segment: put the playhead inside the segment, away from its edges.`,
  lastSegment:
    "A clip needs at least one segment: the last one cannot be deleted.",
  minLength: `A segment must be at least ${MIN_EDIT_SEGMENT_SECONDS} s long.`,
  maxSegments: `A clip holds at most ${MAX_SEGMENTS} segments: delete one before splitting.`,
  segmentMove:
    "Segments sit back to back on the reel in source order: drag their edges to trim or extend them.",
  captionTiming:
    "Captions follow the speech: edit their text, not their timing.",
  voiceoverLength: "A voice-over's length is its audio: move it instead.",
  crossRole: "Items move only between tracks of their own kind.",
  mute: "Only the video track can be muted (it mutes the source audio).",
  captionText: "Captions take their text only.",
  overlayText: "Text overlays take text, size, colour and position only.",
  textLength: `At most ${MAX_TEXT_CHARS} characters.`,
  lineBreaks: `At most ${MAX_OVERLAY_LINES} line breaks.`,
  color: `A colour is at most ${MAX_COLOR_CHARS} characters.`,
  captionWords: `A caption holds at most ${MAX_CAPTION_WORDS} words.`,
  captionWordsTotal: `The captions hold at most ${MAX_TIMELINE_CAPTION_WORDS} words in all.`,
  overlayCharsTotal: `The text overlays hold at most ${MAX_TIMELINE_OVERLAY_CHARS} characters in all.`,
  notFound: "That item is no longer on the timeline.",
  serverVoiceover:
    "Voice-overs are made on the server: use the Voice-over panel.",
  addRole: "Only text overlays can be added on the timeline.",
  removeSegmentsTrack: "The video track cannot be removed.",
  overlayLength: `Text must stay on screen at least ${MIN_SEGMENT_SECONDS} s.`,
} as const;

interface TimelineStore {
  // Private track storage (loaded order; toTimeline writes tracks in this order)
  _tracks: EditorTrack[];
  history: EditorSnapshot[];
  redoStack: EditorSnapshot[];

  // Always returns properly ordered tracks for display (OpenCut's sort)
  tracks: EditorTrack[];
  captionStyle: EditorState["captionStyle"];
  timelineOrigin: EditorState["timelineOrigin"];

  /** The clip's window in its source (converts a v1 edit). */
  sourceWindow: SourceWindow | null;
  /** The source length bounding an extension (the furthest segment end until known). */
  sourceDuration: number | null;
  /**
   * The source's length from the preview's `<video>` metadata (data-model §3: it
   * wins over the job's recorded length); kept across `load`, cleared with the editor.
   */
  probedDuration: number | null;
  /** Why the edit is read-only (a v1 edit that cannot be converted), else null. */
  readOnly: string | null;
  /** The loaded timeline while read-only: written back unchanged. */
  readOnlyTimeline: TimelinePayload | null;
  /** How the loaded edit was converted (null when read-only or empty). */
  converted: AdapterConversion | null;

  // Manual method if you need to force recomputation
  getSortedTracks: () => EditorTrack[];

  // Multi-selection
  selectedElements: Selection[];
  selectElement: (trackId: string, elementId: string, multi?: boolean) => void;
  deselectElement: (trackId: string, elementId: string) => void;
  clearSelectedElements: () => void;
  setSelectedElements: (elements: Selection[]) => void;

  // Drag state
  dragState: {
    isDragging: boolean;
    elementId: string | null;
    trackId: string | null;
    startMouseX: number;
    startElementTime: number;
    clickOffsetTime: number;
    currentTime: number;
  };
  setDragState: (dragState: Partial<TimelineStore["dragState"]>) => void;
  startDrag: (
    elementId: string,
    trackId: string,
    startMouseX: number,
    startElementTime: number,
    clickOffsetTime: number,
  ) => EditResult;
  updateDragTime: (currentTime: number) => void;
  endDrag: () => void;

  // Actions
  /** A new empty track of the role ("" while read-only); written once it holds something. */
  addTrack: (role: "overlays" | "voiceovers") => string;
  insertTrackAt: (role: "overlays" | "voiceovers", index: number) => string;
  removeTrack: (trackId: string) => EditResult;
  addElementToTrack: (
    trackId: string,
    element: CreateTimelineElement,
  ) => EditResult;
  removeElementFromTrack: (trackId: string, elementId: string) => EditResult;
  moveElementToTrack: (
    fromTrackId: string,
    toTrackId: string,
    elementId: string,
  ) => EditResult;
  updateElementTrim: (
    trackId: string,
    elementId: string,
    trimStart: number,
    trimEnd: number,
  ) => EditResult;
  updateElementDuration: (
    trackId: string,
    elementId: string,
    duration: number,
  ) => EditResult;
  updateElementStartTime: (
    trackId: string,
    elementId: string,
    startTime: number,
  ) => EditResult;
  toggleTrackMute: (trackId: string) => EditResult;

  // Split operations for segments (through timelineEdit)
  splitElement: (
    trackId: string,
    elementId: string,
    splitTime: number,
  ) => SplitResult;
  splitAndKeepLeft: (
    trackId: string,
    elementId: string,
    splitTime: number,
  ) => EditResult;
  splitAndKeepRight: (
    trackId: string,
    elementId: string,
    splitTime: number,
  ) => EditResult;
  /** Moves a segment's edges, source seconds; an extension stops at the source and the neighbours. */
  trimSegment: (
    elementId: string,
    edges: { start?: number; end?: number },
  ) => EditResult;
  deleteSegment: (elementId: string) => EditResult;

  // Computed values
  getTotalDuration: () => number;
  /** The edited reel's length: the segments back to back. */
  getReelDuration: () => number;

  // History actions
  undo: () => void;
  redo: () => void;
  pushHistory: () => void;

  // Loading and the schema v2 form
  load: (timeline: TimelinePayload, ctx: AdapterContext) => AdapterResult;
  /**
   * A whole v2 timeline as one undoable step: an `edit` (the 016 tool panels;
   * the segments it adds or changes must keep the editor's minimum and the
   * server's segment rules, else it is refused with the reason) or the
   * `server`'s answer (a voice-over appended: taken as it is).
   */
  replaceFromTimeline: (
    timeline: TimelinePayload,
    from?: "edit" | "server",
  ) => EditResult;
  /** A whole v2 timeline with no history step (adopting the server's copy of a save). */
  adoptTimeline: (timeline: TimelinePayload) => EditResult;
  /**
   * A source length: `probe` is the preview's `<video>` metadata, `job` the
   * job's recorded duration (yt-dlp's), used only until a probe arrives.
   * Re-derives the segments' trims; no history step.
   */
  setSourceDuration: (duration: number, from: "probe" | "job") => void;
  toTimeline: () => TimelinePayload;
  clearTimeline: () => void;
  updateTextElement: (
    trackId: string,
    elementId: string,
    updates: TextUpdates,
  ) => EditResult;
}

const IDLE_DRAG: TimelineStore["dragState"] = {
  isDragging: false,
  elementId: null,
  trackId: null,
  startMouseX: 0,
  startElementTime: 0,
  clickOffsetTime: 0,
  currentTime: 0,
};

const EMPTY_STATE: EditorState = {
  tracks: [makeTrack("segments", null, [])],
  captionStyle: undefined,
  timelineOrigin: { schema_version: 2 },
};

/**
 * Element and track ids survive a rebuild from schema v2: tracks by their
 * place among tracks of their role; segments by the ids the action names;
 * other elements by position when their track kept its length.
 */
function reuseIds(
  prev: EditorTrack[],
  next: EditorTrack[],
  segmentIds?: string[],
): EditorTrack[] {
  const seen = new Map<TrackRole, number>();
  return next.map((track) => {
    const nth = seen.get(track.role) ?? 0;
    seen.set(track.role, nth + 1);
    const old = prev.filter((t) => t.role === track.role)[nth];
    let elements = track.elements;
    if (
      track.role === "segments" &&
      segmentIds &&
      segmentIds.length === elements.length
    ) {
      elements = elements.map((el, i) => ({ ...el, id: segmentIds[i] }));
    } else if (old && old.elements.length === elements.length) {
      elements = elements.map((el, i) => ({ ...el, id: old.elements[i].id }));
    }
    return { ...track, id: old ? old.id : track.id, elements };
  });
}

function keepExisting(selection: Selection[], tracks: EditorTrack[]) {
  return selection.filter(({ trackId, elementId }) =>
    tracks.some(
      (t) => t.id === trackId && t.elements.some((el) => el.id === elementId),
    ),
  );
}

export const useTimelineStore = create<TimelineStore>((set, get) => {
  const snapshot = (): EditorSnapshot => {
    const { _tracks, captionStyle, timelineOrigin } = get();
    return { tracks: _tracks, captionStyle, timelineOrigin };
  };

  // Helper to update tracks and maintain ordering
  const apply = (state: EditorState) => {
    set((s) => ({
      _tracks: state.tracks,
      tracks: sortTracksByOrder(state.tracks) as EditorTrack[],
      captionStyle: state.captionStyle,
      timelineOrigin: state.timelineOrigin,
      selectedElements: keepExisting(s.selectedElements, state.tracks),
    }));
  };

  /** One undoable change: the current state goes on the history, redo is cleared. */
  const commit = (state: EditorState) => {
    get().pushHistory();
    apply(state);
  };

  const commitTracks = (tracks: EditorTrack[]) =>
    commit({ ...snapshot(), tracks });

  const ctx = (): AdapterContext => ({
    sourceWindow: get().sourceWindow,
    sourceDuration: get().sourceDuration,
  });

  /** A v2 timeline as editor state, ids kept; the source bound grows with it. */
  const rebuild = (
    timeline: TimelinePayload,
    segmentIds?: string[],
  ): { ok: true; state: EditorState } | { ok: false; reason: string } => {
    const result = fromTimeline(timeline, ctx());
    if (!result.ok) return result;
    const tracks = reuseIds(get()._tracks, result.state.tracks, segmentIds);
    const duration = tracks.find((t) => t.role === "segments")?.elements[0]
      ?.duration;
    if (duration !== undefined && duration > (get().sourceDuration ?? 0)) {
      set({ sourceDuration: duration });
    }
    return { ok: true, state: { ...result.state, tracks } };
  };

  const find = (trackId: string, elementId: string) => {
    const track = get()._tracks.find((t) => t.id === trackId);
    const element = track?.elements.find((el) => el.id === elementId);
    return track && element ? { track, element } : null;
  };

  const segmentsTrack = () => get()._tracks.find((t) => t.role === "segments");

  const findSegment = (elementId: string) => {
    const track = segmentsTrack();
    const index = track?.elements.findIndex((el) => el.id === elementId) ?? -1;
    return track && index >= 0
      ? { track, index, element: track.elements[index] }
      : null;
  };

  const reelEnd = () =>
    (segmentsTrack()?.elements ?? []).reduce(
      (sum, el) => sum + Math.max(0, visibleLength(el)),
      0,
    );

  /**
   * A new timeline from a segment edit, as one undo step; refused (nothing
   * changes) when its segments as written break a server rule (count, 0.05 s
   * on raw floats, reel length), so a save never meets a 422 for them.
   */
  const commitSegmentEdit = (next: TimelinePayload, segmentIds: string[]) => {
    const refusal = segmentsRefusal(segmentsOf(next));
    if (refusal) return refuse(refusal);
    const rebuilt = rebuild(next, segmentIds);
    if (!rebuilt.ok) return rebuilt;
    commit(rebuilt.state);
    return OK;
  };

  /** A segment edit as new v2 segments (016 setSegments: reel items follow the source). */
  const commitSegments = (segments: Segment[], segmentIds: string[]) =>
    commitSegmentEdit(setSegments(get().toTimeline(), segments), segmentIds);

  /** The segment under `splitTime` (reel s), its source cut, or why not. */
  const splitTarget = (
    trackId: string,
    elementId: string,
    splitTime: number,
  ) => {
    const found = find(trackId, elementId);
    if (!found) return refuse(REFUSALS.notFound);
    if (found.element.role !== "segments") return refuse(REFUSALS.splitRole);
    const segment = findSegment(elementId);
    if (!segment) return refuse(REFUSALS.notFound);
    const el = segment.element;
    const offset = splitTime - el.startTime;
    if (!(offset > 0) || !(offset < visibleLength(el))) {
      return refuse(REFUSALS.splitEdge);
    }
    const segments = segmentsOf(get().toTimeline());
    const source = segments[segment.index];
    // Both parts keep the editor's minimum as written (ms): 51 ms, never under 0.05 s in float math.
    const cut = splitPoint(source, offset);
    if (cut === null) return refuse(REFUSALS.splitEdge);
    return { ok: true as const, segment, segments, source, cut };
  };

  const textGuard = (el: EditorElement, updates: TextUpdates) => {
    const keys = Object.keys(updates) as Array<keyof TextUpdates>;
    const allowed: Array<keyof TextUpdates> =
      el.role === "captions"
        ? ["content"]
        : ["content", "fontSize", "color", "x", "y"];
    if (keys.some((k) => !allowed.includes(k))) {
      return refuse(
        el.role === "captions" ? REFUSALS.captionText : REFUSALS.overlayText,
      );
    }
    const content = updates.content;
    if (content !== undefined) {
      if (content.length > MAX_TEXT_CHARS) return refuse(REFUSALS.textLength);
      if (
        el.role === "overlays" &&
        (content.match(/\n/g) ?? []).length > MAX_OVERLAY_LINES
      ) {
        return refuse(REFUSALS.lineBreaks);
      }
      // The timeline's totals: every other item of the role, plus this one's new text.
      const others = get()
        ._tracks.filter((t) => t.role === el.role)
        .flatMap((t) => t.elements)
        .filter((other) => other.id !== el.id && other.type === "text");
      const textOf = (other: EditorElement) =>
        other.type === "text" ? other.content : "";
      if (el.role === "captions") {
        const words = wordCount(content);
        if (words > MAX_CAPTION_WORDS) return refuse(REFUSALS.captionWords);
        const total = others.reduce((n, o) => n + wordCount(textOf(o)), words);
        if (total > MAX_TIMELINE_CAPTION_WORDS)
          return refuse(REFUSALS.captionWordsTotal);
      } else {
        const total = others.reduce(
          (n, o) => n + textOf(o).length,
          content.length,
        );
        if (total > MAX_TIMELINE_OVERLAY_CHARS)
          return refuse(REFUSALS.overlayCharsTotal);
      }
    }
    if (updates.color !== undefined && updates.color.length > MAX_COLOR_CHARS) {
      return refuse(REFUSALS.color);
    }
    return OK;
  };

  return {
    _tracks: EMPTY_STATE.tracks,
    tracks: EMPTY_STATE.tracks,
    captionStyle: EMPTY_STATE.captionStyle,
    timelineOrigin: EMPTY_STATE.timelineOrigin,
    history: [],
    redoStack: [],
    selectedElements: [],
    sourceWindow: null,
    sourceDuration: null,
    probedDuration: null,
    readOnly: null,
    readOnlyTimeline: null,
    converted: null,

    getSortedTracks: () => sortTracksByOrder(get()._tracks) as EditorTrack[],

    pushHistory: () => {
      const { history } = get();
      set({
        history: [...history, snapshot()].slice(-HISTORY_LIMIT),
        redoStack: [],
      });
    },

    undo: () => {
      const { history, redoStack } = get();
      if (history.length === 0) return;
      const prev = history[history.length - 1];
      set({
        history: history.slice(0, -1),
        redoStack: [...redoStack, snapshot()],
      });
      apply(prev);
    },

    redo: () => {
      const { history, redoStack } = get();
      if (redoStack.length === 0) return;
      const next = redoStack[redoStack.length - 1];
      // Unlike upstream, the state redo leaves goes back on the history.
      set({
        history: [...history, snapshot()].slice(-HISTORY_LIMIT),
        redoStack: redoStack.slice(0, -1),
      });
      apply(next);
    },

    selectElement: (trackId, elementId, multi = false) => {
      set((state) => {
        const exists = state.selectedElements.some(
          (c) => c.trackId === trackId && c.elementId === elementId,
        );
        if (multi) {
          return exists
            ? {
                selectedElements: state.selectedElements.filter(
                  (c) => !(c.trackId === trackId && c.elementId === elementId),
                ),
              }
            : {
                selectedElements: [
                  ...state.selectedElements,
                  { trackId, elementId },
                ],
              };
        } else {
          return { selectedElements: [{ trackId, elementId }] };
        }
      });
    },

    deselectElement: (trackId, elementId) => {
      set((state) => ({
        selectedElements: state.selectedElements.filter(
          (c) => !(c.trackId === trackId && c.elementId === elementId),
        ),
      }));
    },

    clearSelectedElements: () => {
      set({ selectedElements: [] });
    },

    setSelectedElements: (elements) => set({ selectedElements: elements }),

    addTrack: (role) => {
      if (get().readOnly) return "";
      const track = makeTrack(role, null, []);
      commitTracks([...get()._tracks, track]);
      return track.id;
    },

    insertTrackAt: (role, index) => {
      if (get().readOnly) return "";
      const track = makeTrack(role, null, []);
      const tracks = [...get()._tracks];
      tracks.splice(index, 0, track);
      commitTracks(tracks);
      return track.id;
    },

    removeTrack: (trackId) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const track = get()._tracks.find((t) => t.id === trackId);
      if (!track) return refuse(REFUSALS.notFound);
      if (track.role === "segments")
        return refuse(REFUSALS.removeSegmentsTrack);
      commitTracks(get()._tracks.filter((t) => t.id !== trackId));
      return OK;
    },

    addElementToTrack: (trackId, elementData) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const track = get()._tracks.find((t) => t.id === trackId);
      if (!track) return refuse(REFUSALS.notFound);
      if (track.role === "voiceovers") return refuse(REFUSALS.serverVoiceover);
      if (track.role !== "overlays" || elementData.type !== "text") {
        return refuse(REFUSALS.addRole);
      }
      const guard = textGuard(
        { ...elementData, role: "overlays" } as OverlayElement,
        { content: elementData.content },
      );
      if (!guard.ok) return guard;
      const length = Math.max(MIN_SEGMENT_SECONDS, elementData.duration);
      const element: OverlayElement = {
        ...elementData,
        id: crypto.randomUUID(),
        startTime: clamp(
          elementData.startTime || 0,
          0,
          Math.max(0, reelEnd() - length),
        ),
        duration: length,
        trimStart: 0,
        trimEnd: 0,
        fontSize: clampFontSize(elementData.fontSize),
        role: "overlays",
        // A new item: no server fields to keep; toTimeline writes start, end and text.
        origin: {} as OverlayElement["origin"],
      };
      commitTracks(
        get()._tracks.map((t) =>
          t.id === trackId ? { ...t, elements: [...t.elements, element] } : t,
        ),
      );
      return OK;
    },

    removeElementFromTrack: (trackId, elementId) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const found = find(trackId, elementId);
      if (!found) return refuse(REFUSALS.notFound);
      if (found.element.role === "segments")
        return get().deleteSegment(elementId);
      // Empty tracks stay: they are part of the edit (upstream dropped them).
      commitTracks(
        get()._tracks.map((track) =>
          track.id === trackId
            ? {
                ...track,
                elements: track.elements.filter((el) => el.id !== elementId),
              }
            : track,
        ),
      );
      return OK;
    },

    moveElementToTrack: (fromTrackId, toTrackId, elementId) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const found = find(fromTrackId, elementId);
      const toTrack = get()._tracks.find((track) => track.id === toTrackId);
      if (!found || !toTrack) return refuse(REFUSALS.notFound);
      const { element } = found;
      if (element.role === "segments") return refuse(REFUSALS.segmentMove);
      if (element.role === "captions") return refuse(REFUSALS.captionTiming);
      if (toTrack.role !== element.role) return refuse(REFUSALS.crossRole);
      if (fromTrackId === toTrackId) return OK;
      commitTracks(
        get()._tracks.map((track) => {
          if (track.id === fromTrackId) {
            return {
              ...track,
              elements: track.elements.filter((el) => el.id !== elementId),
            };
          } else if (track.id === toTrackId) {
            return { ...track, elements: [...track.elements, element] };
          }
          return track;
        }),
      );
      return OK;
    },

    updateElementTrim: (trackId, elementId, trimStart, trimEnd) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const found = find(trackId, elementId);
      if (!found) return refuse(REFUSALS.notFound);
      const { element } = found;
      if (element.role === "segments") {
        // OpenCut's edge resize on a segment: trims are source seconds.
        return get().trimSegment(elementId, {
          start: trimStart,
          end: element.duration - trimEnd,
        });
      }
      if (element.role === "captions") return refuse(REFUSALS.captionTiming);
      if (element.role === "voiceovers")
        return refuse(REFUSALS.voiceoverLength);
      // An overlay: at least 0.05 s visible, ending no later than the reel.
      const start = Math.max(0, trimStart);
      const overrun = Math.max(
        0,
        element.startTime + element.duration - start - trimEnd - reelEnd(),
      );
      const end = Math.max(0, trimEnd) + overrun;
      if (element.duration - start - end < MIN_SEGMENT_SECONDS - EPS) {
        return refuse(REFUSALS.overlayLength);
      }
      if (start === element.trimStart && end === element.trimEnd) return OK;
      commitTracks(
        get()._tracks.map((track) =>
          track.id === trackId
            ? {
                ...track,
                elements: track.elements.map((el) =>
                  el.id === elementId
                    ? { ...el, trimStart: start, trimEnd: end }
                    : el,
                ),
              }
            : track,
        ),
      );
      return OK;
    },

    updateElementDuration: (trackId, elementId, duration) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const found = find(trackId, elementId);
      if (!found) return refuse(REFUSALS.notFound);
      const { element } = found;
      if (element.role === "segments") return refuse(REFUSALS.segmentMove);
      if (element.role === "captions") return refuse(REFUSALS.captionTiming);
      if (element.role === "voiceovers")
        return refuse(REFUSALS.voiceoverLength);
      const trims = element.trimStart + element.trimEnd;
      const next = clamp(
        duration,
        MIN_SEGMENT_SECONDS + trims,
        Math.max(MIN_SEGMENT_SECONDS, reelEnd() - element.startTime) + trims,
      );
      if (next === element.duration) return OK;
      commitTracks(
        get()._tracks.map((track) =>
          track.id === trackId
            ? {
                ...track,
                elements: track.elements.map((el) =>
                  el.id === elementId ? { ...el, duration: next } : el,
                ),
              }
            : track,
        ),
      );
      return OK;
    },

    updateElementStartTime: (trackId, elementId, startTime) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const found = find(trackId, elementId);
      if (!found) return refuse(REFUSALS.notFound);
      const { element } = found;
      if (element.role === "segments") return refuse(REFUSALS.segmentMove);
      if (element.role === "captions") return refuse(REFUSALS.captionTiming);
      // An overlay or voice-over stays on the reel: [0, reel end - its length].
      const next = clamp(
        startTime,
        0,
        Math.max(0, reelEnd() - visibleLength(element)),
      );
      if (next === element.startTime) return OK;
      commitTracks(
        get()._tracks.map((track) =>
          track.id === trackId
            ? {
                ...track,
                elements: track.elements.map((el) =>
                  el.id === elementId ? { ...el, startTime: next } : el,
                ),
              }
            : track,
        ),
      );
      return OK;
    },

    toggleTrackMute: (trackId) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const track = get()._tracks.find((t) => t.id === trackId);
      if (!track) return refuse(REFUSALS.notFound);
      if (track.role !== "segments") return refuse(REFUSALS.mute);
      commitTracks(
        get()._tracks.map((t) =>
          t.id === trackId ? { ...t, muted: !t.muted } : t,
        ),
      );
      return OK;
    },

    updateTextElement: (trackId, elementId, updates) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const found = find(trackId, elementId);
      if (!found) return refuse(REFUSALS.notFound);
      const { element } = found;
      if (element.type !== "text") {
        return refuse(REFUSALS.overlayText);
      }
      const guard = textGuard(element, updates);
      if (!guard.ok) return guard;
      const next = { ...element, ...updates };
      if (updates.fontSize !== undefined)
        next.fontSize = clampFontSize(updates.fontSize);
      if (updates.x !== undefined)
        next.x = clamp(
          updates.x,
          -OVERLAY_CANVAS.width / 2,
          OVERLAY_CANVAS.width / 2,
        );
      if (updates.y !== undefined)
        next.y = clamp(
          updates.y,
          -OVERLAY_CANVAS.height / 2,
          OVERLAY_CANVAS.height / 2,
        );
      const changed = (Object.keys(updates) as Array<keyof TextUpdates>).some(
        (k) => next[k] !== element[k],
      );
      if (!changed) return OK;
      commitTracks(
        get()._tracks.map((track) =>
          track.id === trackId
            ? {
                ...track,
                elements: track.elements.map((el) =>
                  el.id === elementId ? (next as EditorElement) : el,
                ),
              }
            : track,
        ),
      );
      return OK;
    },

    splitElement: (trackId, elementId, splitTime) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const target = splitTarget(trackId, elementId, splitTime);
      if (!target.ok) return target;
      if (target.segments.length >= MAX_SEGMENTS)
        return refuse(REFUSALS.maxSegments);
      // The 016 rule (R4): reel items follow the source; OpenCut split one element only.
      const next = splitAt(get().toTimeline(), splitTime);
      if (!next) return refuse(REFUSALS.splitEdge);
      const secondElementId = crypto.randomUUID();
      const ids = target.segment.track.elements.map((el) => el.id);
      ids.splice(target.segment.index + 1, 0, secondElementId);
      const committed = commitSegmentEdit(next, ids);
      return committed.ok ? { ok: true, id: secondElementId } : committed;
    },

    // Split a segment and keep only the left portion
    splitAndKeepLeft: (trackId, elementId, splitTime) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const target = splitTarget(trackId, elementId, splitTime);
      if (!target.ok) return target;
      const { segment, segments, source, cut } = target;
      const next = segments.map((s, i) =>
        i === segment.index ? { start: source.start, end: cut } : s,
      );
      return commitSegments(
        next,
        segment.track.elements.map((el) => el.id),
      );
    },

    // Split a segment and keep only the right portion
    splitAndKeepRight: (trackId, elementId, splitTime) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const target = splitTarget(trackId, elementId, splitTime);
      if (!target.ok) return target;
      const { segment, segments, source, cut } = target;
      const next = segments.map((s, i) =>
        i === segment.index ? { start: cut, end: source.end } : s,
      );
      return commitSegments(
        next,
        segment.track.elements.map((el) => el.id),
      );
    },

    trimSegment: (elementId, edges) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const segment = findSegment(elementId);
      if (!segment) return refuse(REFUSALS.notFound);
      const segments = segmentsOf(get().toTimeline());
      const current = segments[segment.index];
      const wantStart = edges.start ?? current.start;
      const wantEnd = edges.end ?? current.end;
      if (!Number.isFinite(wantStart) || !Number.isFinite(wantEnd)) {
        return refuse(REFUSALS.minLength);
      }
      // An extension stops at the source's bounds and the neighbours' source windows.
      const lo = segment.index > 0 ? segments[segment.index - 1].end : 0;
      const hi =
        segment.index < segments.length - 1
          ? segments[segment.index + 1].start
          : (get().sourceDuration ?? current.end);
      // Checked as written (setSegments rounds to the ms): 51 ms, never under 0.05 s in float math.
      const start = round3(Math.max(lo, round3(wantStart)));
      const end = round3(Math.min(hi, round3(wantEnd)));
      if (!longEnough(start, end)) {
        return refuse(REFUSALS.minLength);
      }
      if (start === current.start && end === current.end) return OK;
      const next = segments.map((s, i) =>
        i === segment.index ? { start, end } : s,
      );
      return commitSegments(
        next,
        segment.track.elements.map((el) => el.id),
      );
    },

    deleteSegment: (elementId) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const segment = findSegment(elementId);
      if (!segment) return refuse(REFUSALS.notFound);
      const segments = segmentsOf(get().toTimeline());
      if (segments.length <= 1) return refuse(REFUSALS.lastSegment);
      return commitSegments(
        segments.filter((_, i) => i !== segment.index),
        segment.track.elements
          .map((el) => el.id)
          .filter((id) => id !== elementId),
      );
    },

    getTotalDuration: () => {
      const { _tracks } = get();
      if (_tracks.length === 0) return 0;

      const trackEndTimes = _tracks.map((track) =>
        track.elements.reduce((maxEnd, element) => {
          const elementEnd =
            element.startTime +
            element.duration -
            element.trimStart -
            element.trimEnd;
          return Math.max(maxEnd, elementEnd);
        }, 0),
      );

      return Math.max(...trackEndTimes, 0);
    },

    getReelDuration: () => reelEnd(),

    dragState: IDLE_DRAG,

    setDragState: (dragState) =>
      set((state) => ({
        dragState: { ...state.dragState, ...dragState },
      })),

    startDrag: (
      elementId,
      trackId,
      startMouseX,
      startElementTime,
      clickOffsetTime,
    ) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const found = find(trackId, elementId);
      if (!found) return refuse(REFUSALS.notFound);
      if (found.element.role === "segments")
        return refuse(REFUSALS.segmentMove);
      if (found.element.role === "captions")
        return refuse(REFUSALS.captionTiming);
      set({
        dragState: {
          isDragging: true,
          elementId,
          trackId,
          startMouseX,
          startElementTime,
          clickOffsetTime,
          currentTime: startElementTime,
        },
      });
      return OK;
    },

    updateDragTime: (currentTime) => {
      set((state) => ({
        dragState: {
          ...state.dragState,
          currentTime,
        },
      }));
    },

    endDrag: () => {
      set({ dragState: IDLE_DRAG });
    },

    load: (timeline, loadContext) => {
      // The source's metadata may have arrived before the edit: it bounds the segments.
      const probed = get().probedDuration;
      const context =
        probed === null
          ? loadContext
          : { ...loadContext, sourceDuration: probed };
      const result = fromTimeline(timeline, context);
      let state: EditorState;
      if (result.ok) {
        state = result.state;
      } else {
        // Read-only: shown as best it can be (v1 reel pieces as segments), written back as loaded.
        const display = fromTimeline(
          { ...timeline, schema_version: 2 },
          context,
        );
        state = display.ok ? display.state : EMPTY_STATE;
      }
      const segmentDuration = state.tracks.find((t) => t.role === "segments")
        ?.elements[0]?.duration;
      set({
        _tracks: state.tracks,
        tracks: sortTracksByOrder(state.tracks) as EditorTrack[],
        captionStyle: state.captionStyle,
        timelineOrigin: state.timelineOrigin,
        sourceWindow: context.sourceWindow,
        sourceDuration: segmentDuration ?? context.sourceDuration,
        readOnly: result.ok ? null : result.reason,
        readOnlyTimeline: result.ok ? null : timeline,
        converted: result.ok ? result.converted : null,
        history: [],
        redoStack: [],
        selectedElements: [],
        dragState: IDLE_DRAG,
      });
      return result;
    },

    replaceFromTimeline: (timeline, from = "edit") => {
      if (get().readOnly) return refuse(get().readOnly as string);
      if (from === "edit") {
        const refusal = segmentEditRefusal(
          segmentsOf(get().toTimeline()),
          segmentsOf(timeline),
        );
        if (refusal) return refuse(refusal);
      }
      const rebuilt = rebuild(timeline);
      if (!rebuilt.ok) return rebuilt;
      commit(rebuilt.state);
      return OK;
    },

    adoptTimeline: (timeline) => {
      if (get().readOnly) return refuse(get().readOnly as string);
      const rebuilt = rebuild(timeline);
      if (!rebuilt.ok) return rebuilt;
      apply(rebuilt.state);
      return OK;
    },

    setSourceDuration: (duration, from) => {
      if (!(duration > 0)) return;
      // The source's own metadata wins over the job's record, whichever came first.
      if (from === "job" && get().probedDuration !== null) return;
      if (from === "probe") set({ probedDuration: duration });
      const current = get().sourceDuration;
      if (get().readOnly) {
        set({ sourceDuration: Math.max(duration, current ?? 0) });
        return;
      }
      const furthest = Math.max(
        0,
        ...segmentsOf(get().toTimeline()).map((s) => s.end),
      );
      const next = Math.max(duration, furthest);
      if (next === current) return;
      set({ sourceDuration: next });
      const rebuilt = rebuild(
        get().toTimeline(),
        segmentsTrack()?.elements.map((el) => el.id),
      );
      if (rebuilt.ok) apply(rebuilt.state);
    },

    toTimeline: () => get().readOnlyTimeline ?? stateToTimeline(snapshot()),

    clearTimeline: () => {
      set({
        _tracks: EMPTY_STATE.tracks,
        tracks: EMPTY_STATE.tracks,
        captionStyle: EMPTY_STATE.captionStyle,
        timelineOrigin: EMPTY_STATE.timelineOrigin,
        sourceWindow: null,
        sourceDuration: null,
        probedDuration: null,
        readOnly: null,
        readOnlyTimeline: null,
        converted: null,
        history: [],
        redoStack: [],
        selectedElements: [],
        dragState: IDLE_DRAG,
      });
    },
  };
});
