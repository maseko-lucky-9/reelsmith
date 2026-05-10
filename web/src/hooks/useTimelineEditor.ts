/**
 * Inline-editor timeline state hook (W1.13).
 *
 * Manages a TimelinePayload locally with undo/redo history. Save
 * persists via api.upsertClipEdit and bumps version optimistically.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type { ClipEditState, TimelinePayload } from '@/api/client'

const HISTORY_LIMIT = 50

const EMPTY_TIMELINE: TimelinePayload = {
  tracks: [
    { kind: 'video', items: [{ start: 0, end: 0, src: 'main' }] },
    { kind: 'caption', items: [] },
    { kind: 'text-overlay', items: [] },
  ],
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
}

export function useTimelineEditor(clipId: string): UseTimelineEditor {
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

  const saveMutation = useMutation({
    mutationFn: () =>
      api.upsertClipEdit(clipId, {
        timeline,
        version: version || undefined,
      }),
    onSuccess: (state) => {
      setVersion(state.version)
      setSavedSnapshot(JSON.stringify(state.timeline))
      queryClient.setQueryData(['clip-edit', clipId], state)
    },
  })

  const save = useCallback(async () => {
    await saveMutation.mutateAsync()
  }, [saveMutation])

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
  }
}
