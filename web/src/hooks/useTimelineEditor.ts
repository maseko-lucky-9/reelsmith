/**
 * Inline-editor timeline state hook (W1.13; schema v2, spec 016).
 *
 * Manages a TimelinePayload locally with undo/redo history. Save
 * persists via api.upsertClipEdit and bumps version optimistically.
 * A v1 timeline loads as stored and is sent as schema v2 on save when the
 * clip's source window is known (`prepareForSave`).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { ClipEditState, EditWord, TimelinePayload } from '@/api/client'
import { prepareForSave, type SourceWindow } from '@/lib/timelineEdit'

const HISTORY_LIMIT = 50

const EMPTY_TIMELINE: TimelinePayload = {
  tracks: [
    { kind: 'video', items: [{ start: 0, end: 0, src: 'main' }] },
    { kind: 'caption', items: [] },
    { kind: 'text-overlay', items: [] },
  ],
}

const NO_WORDS: EditWord[] = []

export interface UseTimelineEditorOptions {
  /** The clip's window in its source video; needed to save a v1 timeline as v2. */
  sourceWindow?: SourceWindow | null
}

export interface UseTimelineEditor {
  timeline: TimelinePayload
  version: number
  isDirty: boolean
  isSaving: boolean
  saveError: string | null
  setTimeline: (next: TimelinePayload) => void
  undo: () => void
  redo: () => void
  save: () => Promise<void>
  canUndo: boolean
  canRedo: boolean
  /** The clip's word timings from the edit API (empty until loaded). */
  words?: EditWord[]
  /** Adopts an edit the server changed (e.g. a voice-over appended), as saved. */
  applyServerState?: (state: ClipEditState) => void
}

export function useTimelineEditor(
  clipId: string,
  options: UseTimelineEditorOptions = {},
): UseTimelineEditor {
  const queryClient = useQueryClient()
  const editQuery = useQuery({
    queryKey: ['clip-edit', clipId],
    // Treat 404 as "no edit yet" — surface the empty timeline.
    queryFn: async () => {
      try {
        return await api.getClipEdit(clipId)
      } catch (e) {
        if (String(e).includes('404')) return null
        throw e
      }
    },
  })

  const [history, setHistory] = useState<TimelinePayload[]>([EMPTY_TIMELINE])
  const [historyIdx, setHistoryIdx] = useState(0)
  const [version, setVersion] = useState<number>(0)
  const [savedSnapshot, setSavedSnapshot] = useState<string>(
    JSON.stringify(EMPTY_TIMELINE),
  )
  const sourceWindow = options.sourceWindow ?? null
  const windowRef = useRef<SourceWindow | null>(sourceWindow)
  useEffect(() => {
    windowRef.current = sourceWindow
  }, [sourceWindow])

  // Hydrate once from server.
  const hydratedRef = useRef(false)
  useEffect(() => {
    if (hydratedRef.current) return
    if (editQuery.isLoading) return
    const server: ClipEditState | null = editQuery.data ?? null
    const tl = server?.timeline ?? EMPTY_TIMELINE
    setHistory([tl])
    setHistoryIdx(0)
    setVersion(server?.version ?? 0)
    setSavedSnapshot(JSON.stringify(tl))
    hydratedRef.current = true
  }, [editQuery.data, editQuery.isLoading])

  const timeline = history[historyIdx]

  const setTimeline = useCallback((next: TimelinePayload) => {
    setHistory((prev) => {
      const truncated = prev.slice(0, historyIdx + 1)
      const appended = [...truncated, next]
      return appended.length > HISTORY_LIMIT
        ? appended.slice(appended.length - HISTORY_LIMIT)
        : appended
    })
    setHistoryIdx((idx) => Math.min(idx + 1, HISTORY_LIMIT - 1))
  }, [historyIdx])

  const undo = useCallback(() => {
    setHistoryIdx((idx) => Math.max(0, idx - 1))
  }, [])
  const redo = useCallback(() => {
    setHistoryIdx((idx) => Math.min(history.length - 1, idx + 1))
  }, [history.length])

  /** The server's copy becomes the current entry, so it reads as saved. */
  const adopt = useCallback(
    (state: ClipEditState) => {
      setVersion(state.version)
      setSavedSnapshot(JSON.stringify(state.timeline))
      setHistory((prev) => prev.map((tl, i) => (i === historyIdx ? state.timeline : tl)))
      queryClient.setQueryData(['clip-edit', clipId], (old: ClipEditState | null | undefined) => ({
        ...state,
        words: state.words ?? old?.words,
      }))
    },
    [clipId, historyIdx, queryClient],
  )

  const saveMutation = useMutation({
    mutationFn: () =>
      api.upsertClipEdit(clipId, {
        timeline: prepareForSave(timeline, windowRef.current),
        version: version || undefined,
      }),
    onSuccess: adopt,
  })

  const save = useCallback(async () => {
    await saveMutation.mutateAsync()
  }, [saveMutation])

  const applyServerState = useCallback(
    (state: ClipEditState) => {
      setHistory((prev) => {
        const appended = [...prev.slice(0, historyIdx + 1), state.timeline]
        return appended.length > HISTORY_LIMIT
          ? appended.slice(appended.length - HISTORY_LIMIT)
          : appended
      })
      setHistoryIdx((idx) => Math.min(idx + 1, HISTORY_LIMIT - 1))
      setVersion(state.version)
      setSavedSnapshot(JSON.stringify(state.timeline))
      queryClient.setQueryData(['clip-edit', clipId], (old: ClipEditState | null | undefined) => ({
        ...state,
        words: state.words ?? old?.words,
      }))
    },
    [clipId, historyIdx, queryClient],
  )

  return {
    timeline,
    version,
    isDirty: JSON.stringify(timeline) !== savedSnapshot,
    isSaving: saveMutation.isPending,
    saveError: saveMutation.error ? String(saveMutation.error) : null,
    setTimeline,
    undo,
    redo,
    save,
    canUndo: historyIdx > 0,
    canRedo: historyIdx < history.length - 1,
    words: editQuery.data?.words ?? NO_WORDS,
    applyServerState,
  }
}
