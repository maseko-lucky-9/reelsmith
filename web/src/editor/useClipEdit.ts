/**
 * The clip editor's load / save / adopt hook on the timeline store (spec 017
 * T145; FR-005, FR-012, FR-019, FR-020). Port of spec 016's editor state hook
 * (`web/src/hooks/`, removed by spec 017 T156):
 * the edit lives in the OpenCut timeline store (`useTimelineStore`), whose
 * history is the undo/redo; this hook loads it once from
 * `GET /clips/{id}/edit`, saves it through `PUT /clips/{id}/edit` with the
 * version it was based on, and keeps edits made while a save is in flight.
 *
 * "Saved" is compared in schema v2 form with key order ignored: a v1 edit is
 * compared after conversion (opening it is not a change), and undoing back
 * to the saved copy reads as saved.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { create } from "zustand";
import { api } from "@/api/client";
import type { ClipEditState, EditWord, TimelinePayload } from "@/api/client";
import { apiErrorStatus } from "@/lib/apiErrorDetail";
import {
  isV2,
  prepareForSave,
  upgradeTimeline,
  type SourceWindow,
} from "@/lib/timelineEdit";
import { NO_EDIT_TIMELINE, toTimeline } from "@/editor/adapter";
import {
  useTimelineStore,
  type EditResult,
} from "@/editor/opencut/stores/timeline-store";

const NO_WORDS: EditWord[] = [];

/** JSON with object keys sorted (undefined dropped, as JSON does): equal edits give equal strings. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(value, (_key, v: unknown) =>
    v !== null && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(
          Object.keys(v)
            .sort()
            .map((k) => [k, (v as Record<string, unknown>)[k]]),
        )
      : v,
  );
}

/**
 * What the server holds, as last loaded or saved. A zustand store rather
 * than component state so the hydrate effect updates it together with the
 * timeline store (no React state set inside an effect).
 */
interface SavedEdit {
  /** The clip the edit was loaded for; null until loaded (and after the editor closes). */
  clipId: string | null;
  /** The editor (hook instance) that loaded it: a closed editor's answers are not this one's. */
  owner: symbol | null;
  /** The saved edit's version; 0 for the server's default timeline. */
  version: number;
  /** canonicalJson of the saved edit in its schema v2 form. */
  snapshot: string;
  /** The saved row is schema v1: the voice-over flow saves first. */
  isV1: boolean;
  /** The timeline as loaded when the editor opened. */
  loadedTimeline: TimelinePayload | null;
}

const NOT_LOADED: SavedEdit = {
  clipId: null,
  owner: null,
  version: 0,
  snapshot: "",
  isV1: false,
  loadedTimeline: null,
};

const useSavedEdit = create<SavedEdit>(() => NOT_LOADED);

export interface UseClipEditOptions {
  /** The clip's window in its source video: converts a v1 edit. */
  sourceWindow?: SourceWindow | null;
  /** The job's recorded source length: bounds an extension until the preview's `<video>` metadata gives the real one. */
  sourceDuration?: number | null;
}

export interface UseClipEdit {
  /** The edit as schema v2 (as loaded while read-only). */
  timeline: TimelinePayload;
  version: number;
  isLoaded: boolean;
  isDirty: boolean;
  isSaving: boolean;
  saveError: string | null;
  /** Why the edit cannot be loaded (a failed GET other than 404), else null. */
  loadError: string | null;
  save: () => Promise<void>;
  /** A whole v2 timeline as one undoable step (the 016 tool panels). */
  setTimeline: (next: TimelinePayload) => EditResult;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  /** The clip's word timings from the edit API (empty until loaded). */
  words: EditWord[];
  /**
   * Adopts an edit the server changed (a voice-over appended): one undoable
   * step, saved. False (and only this clip's cached edit is updated) when the
   * editor no longer holds this clip: the answer came after it closed.
   */
  applyServerState: (state: ClipEditState) => boolean;
  /** The timeline as loaded from the server when the editor opened (null until loaded). */
  loadedTimeline: TimelinePayload | null;
  /** The saved row is schema v1 (the server refuses a voice-over on it: save first). */
  savedIsV1: boolean;
  /** Why the edit is read-only (a v1 edit that cannot be converted), else null. */
  readOnly: string | null;
  /**
   * Whether the editor still shows this clip (the singleton stores hold it):
   * false once the page moved to another clip or closed. Read it when an
   * answer arrives, to tell whether news about this clip belongs on screen.
   */
  isShowing: () => boolean;
}

function savedFields(
  state: { timeline: TimelinePayload; version: number },
  window: SourceWindow | null,
) {
  return {
    version: state.version,
    snapshot: canonicalJson(
      upgradeTimeline(state.timeline, window) ?? state.timeline,
    ),
    isV1: state.version > 0 && !isV2(state.timeline),
  };
}

export function useClipEdit(
  clipId: string,
  options: UseClipEditOptions = {},
): UseClipEdit {
  const queryClient = useQueryClient();
  // This editor: a save answering after it closed (even when the same clip
  // was opened again meanwhile) is not the reopened editor's to adopt.
  const [instance] = useState(() => Symbol("clip-edit"));
  const editQuery = useQuery({
    queryKey: ["clip-edit", clipId],
    // 404 is "no edit yet": the editor opens the clip's window.
    queryFn: async () => {
      try {
        return await api.getClipEdit(clipId);
      } catch (e) {
        if (apiErrorStatus(e) === 404) return null;
        throw e;
      }
    },
  });

  const sourceWindow = options.sourceWindow ?? null;
  const sourceDuration = options.sourceDuration ?? null;
  const windowRef = useRef<SourceWindow | null>(sourceWindow);
  useEffect(() => {
    windowRef.current = sourceWindow;
  }, [sourceWindow]);

  // Hydrate once per opening of the editor from the server; again only for
  // a v1 edit that waited for the clip's window. One editor at a time: the
  // timeline store is a singleton.
  const awaitingWindowRef = useRef(false);
  const editData = editQuery.data;
  const editLoaded = editQuery.isSuccess;
  useEffect(() => {
    if (!editLoaded) return;
    if (
      useSavedEdit.getState().clipId === clipId &&
      !(awaitingWindowRef.current && sourceWindow)
    )
      return;
    const server: ClipEditState | null = editData ?? null;
    const timeline = server?.timeline ?? NO_EDIT_TIMELINE;
    const result = useTimelineStore
      .getState()
      .load(timeline, { sourceWindow, sourceDuration });
    useSavedEdit.setState({
      clipId,
      owner: instance,
      ...savedFields({ timeline, version: server?.version ?? 0 }, sourceWindow),
      loadedTimeline: timeline,
    });
    awaitingWindowRef.current =
      !result.ok && sourceWindow === null && !isV2(timeline);
    // sourceDuration is read at load only; later values go through setSourceDuration below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipId, editData, editLoaded, sourceWindow, instance]);

  useEffect(
    () => () => {
      // Closing the editor forgets the edit: reopening it (or StrictMode's
      // second mount) loads the saved edit afresh, and the next editor never
      // shows this one's tracks before its own edit arrives.
      useSavedEdit.setState(NOT_LOADED);
      useTimelineStore.getState().clearTimeline();
    },
    [clipId],
  );

  const hydrated = useSavedEdit((s) => s.clipId === clipId);
  useEffect(() => {
    // The job's record: the store keeps the preview's probed length over it.
    if (hydrated && sourceDuration)
      useTimelineStore.getState().setSourceDuration(sourceDuration, "job");
  }, [hydrated, sourceDuration]);

  const tracks = useTimelineStore((s) => s._tracks);
  const captionStyle = useTimelineStore((s) => s.captionStyle);
  const timelineOrigin = useTimelineStore((s) => s.timelineOrigin);
  const readOnlyTimeline = useTimelineStore((s) => s.readOnlyTimeline);
  const readOnly = useTimelineStore((s) => s.readOnly);
  const canUndo = useTimelineStore((s) => s.history.length > 0);
  const canRedo = useTimelineStore((s) => s.redoStack.length > 0);
  const timeline = useMemo(
    () =>
      readOnlyTimeline ?? toTimeline({ tracks, captionStyle, timelineOrigin }),
    [readOnlyTimeline, tracks, captionStyle, timelineOrigin],
  );
  const version = useSavedEdit((s) => s.version);
  const snapshot = useSavedEdit((s) => s.snapshot);
  const savedIsV1 = useSavedEdit((s) => s.isV1);
  const loadedTimeline = useSavedEdit((s) => s.loadedTimeline);
  const isDirty = useMemo(
    () => hydrated && canonicalJson(timeline) !== snapshot,
    [hydrated, timeline, snapshot],
  );

  // canonicalJson of what the save in flight sent.
  const sentRef = useRef<string | null>(null);

  /** This clip's cached edit becomes the server's answer (words kept when it has none). */
  const cacheAnswer = useCallback(
    (state: ClipEditState) =>
      queryClient.setQueryData(
        ["clip-edit", clipId],
        (old: ClipEditState | null | undefined) => ({
          ...state,
          words: state.words ?? old?.words,
        }),
      ),
    [clipId, queryClient],
  );

  /**
   * A save answered. The version and the saved snapshot always move on. When
   * the edit is still what was sent, the server's copy replaces it (no
   * history step), so it reads as saved; edits made while the save was in
   * flight are kept and stay dirty. The mutation's callbacks outlive the
   * editor: an answer arriving once the singleton stores hold another
   * editor (another clip, none, or this clip opened again: its sent copy is
   * not that editor's) only updates this clip's cache.
   */
  const adopt = useCallback(
    (state: ClipEditState) => {
      const sent = sentRef.current;
      sentRef.current = null;
      cacheAnswer(state);
      if (useSavedEdit.getState().owner !== instance) return;
      const store = useTimelineStore.getState();
      const unchanged =
        sent === null || canonicalJson(store.toTimeline()) === sent;
      useSavedEdit.setState(savedFields(state, windowRef.current));
      if (unchanged) store.adoptTimeline(state.timeline);
    },
    [instance, cacheAnswer],
  );

  const saveMutation = useMutation({
    mutationFn: () => {
      const sent = useTimelineStore.getState().toTimeline();
      sentRef.current = canonicalJson(sent);
      const { version: basedOn } = useSavedEdit.getState();
      const timelineToSend = prepareForSave(sent, windowRef.current);
      // No version for the server's default timeline (version 0).
      return api.upsertClipEdit(
        clipId,
        basedOn > 0
          ? { timeline: timelineToSend, version: basedOn }
          : { timeline: timelineToSend },
      );
    },
    onSuccess: adopt,
  });
  const { mutateAsync } = saveMutation;

  const save = useCallback(async () => {
    await mutateAsync();
  }, [mutateAsync]);

  const applyServerState = useCallback(
    (state: ClipEditState) => {
      cacheAnswer(state);
      // Another clip's editor (or none) holds the stores now: leave them be.
      if (useSavedEdit.getState().clipId !== clipId) return false;
      useTimelineStore.getState().replaceFromTimeline(state.timeline, "server");
      useSavedEdit.setState(savedFields(state, windowRef.current));
      return true;
    },
    [clipId, cacheAnswer],
  );

  const setTimeline = useCallback(
    (next: TimelinePayload) =>
      useTimelineStore.getState().replaceFromTimeline(next),
    [],
  );
  const isShowing = useCallback(
    () => useSavedEdit.getState().clipId === clipId,
    [clipId],
  );
  const undo = useCallback(() => useTimelineStore.getState().undo(), []);
  const redo = useCallback(() => useTimelineStore.getState().redo(), []);

  return {
    timeline,
    version,
    isLoaded: hydrated,
    isDirty,
    isSaving: saveMutation.isPending,
    saveError: saveMutation.error ? String(saveMutation.error) : null,
    loadError: editQuery.error ? String(editQuery.error) : null,
    save,
    setTimeline,
    undo,
    redo,
    canUndo,
    canRedo,
    words: editQuery.data?.words ?? NO_WORDS,
    applyServerState,
    loadedTimeline,
    savedIsV1,
    readOnly,
    isShowing,
  };
}
