/**
 * useTimelineEditor save adoption: a save in flight must not overwrite edits
 * made after it started (controlled-promise mutation).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import type { ClipEditState, TimelinePayload } from '@/api/client'

vi.mock('@/api/client', () => ({
  api: {
    getClipEdit: vi.fn(),
    upsertClipEdit: vi.fn(),
  },
}))

import { api } from '@/api/client'
import { useTimelineEditor } from './useTimelineEditor'

const tl = (text: string): TimelinePayload => ({
  schema_version: 2,
  tracks: [
    { kind: 'video', items: [{ start: 100, end: 110, src: 'main' }] },
    { kind: 'caption', items: [{ start: 0, end: 1, text }] },
  ],
})

const edit = (timeline: TimelinePayload, version: number): ClipEditState => ({
  clip_id: 'clip-1',
  timeline,
  version,
  created_at: 'x',
  updated_at: 'x',
})

function controlled<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: qc }, children)
  return renderHook(() => useTimelineEditor('clip-1'), { wrapper })
}

const captionText = (t: TimelinePayload) => t.tracks.find((k) => k.kind === 'caption')?.items[0]?.text

beforeEach(() => {
  vi.mocked(api.getClipEdit).mockReset().mockResolvedValue(edit(tl('loaded'), 1))
  vi.mocked(api.upsertClipEdit).mockReset()
})

describe('useTimelineEditor — save in flight', () => {
  it('keeps edits made while the save was in flight, still dirty, on the new version', async () => {
    const pending = controlled<ClipEditState>()
    vi.mocked(api.upsertClipEdit).mockReturnValue(pending.promise)
    const { result } = setup()
    await waitFor(() => expect(captionText(result.current.timeline)).toBe('loaded'))

    act(() => result.current.setTimeline(tl('first')))
    let saved!: Promise<void>
    act(() => {
      saved = result.current.save()
    })
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(1))
    expect(captionText(vi.mocked(api.upsertClipEdit).mock.calls[0][1].timeline)).toBe('first')

    // Edited after the save started.
    act(() => result.current.setTimeline(tl('second')))
    await act(async () => {
      pending.resolve(edit(tl('first'), 2))
      await saved
    })

    expect(captionText(result.current.timeline)).toBe('second')
    expect(result.current.version).toBe(2)
    expect(result.current.isDirty).toBe(true)
    // Undo lands on what was saved, which now reads as saved.
    act(() => result.current.undo())
    expect(captionText(result.current.timeline)).toBe('first')
    expect(result.current.isDirty).toBe(false)
  })

  it("adopts the server's copy when nothing changed during the save", async () => {
    const pending = controlled<ClipEditState>()
    vi.mocked(api.upsertClipEdit).mockReturnValue(pending.promise)
    const { result } = setup()
    await waitFor(() => expect(captionText(result.current.timeline)).toBe('loaded'))

    act(() => result.current.setTimeline(tl('first')))
    let saved!: Promise<void>
    act(() => {
      saved = result.current.save()
    })
    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalledTimes(1))
    await act(async () => {
      // The server normalises the edit (e.g. rounds a time).
      pending.resolve(edit(tl('first (server)'), 2))
      await saved
    })

    expect(captionText(result.current.timeline)).toBe('first (server)')
    expect(result.current.version).toBe(2)
    expect(result.current.isDirty).toBe(false)
  })
})
