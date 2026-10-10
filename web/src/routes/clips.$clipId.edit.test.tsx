import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => vi.fn() }
})

vi.mock('@/api/client', () => ({
  api: {
    listClips: vi.fn(),
    rerenderClip: vi.fn(),
  },
}))

vi.mock('@/hooks/useTimelineEditor', () => ({
  useTimelineEditor: () => ({
    timeline: { tracks: [] },
    version: 0,
    isDirty: false,
    isSaving: false,
    saveError: null,
    canUndo: false,
    canRedo: false,
    setTimeline: vi.fn(),
    undo: vi.fn(),
    redo: vi.fn(),
    save: vi.fn(),
  }),
}))

vi.mock('@/components/editor/MultiTrackTimeline', () => ({
  MultiTrackTimeline: () => null,
}))

import { api } from '@/api/client'
import { clipEditRoute } from './clips.$clipId.edit'

const REGEN_LABEL = 'Regenerate title, summary and hashtags'

async function renderPage() {
  vi.mocked(api.listClips).mockResolvedValue([
    { clip_id: 'clip-1', job_id: 'job-1', title: 'Intro', output_path: null } as never,
  ])
  vi.mocked(api.rerenderClip).mockResolvedValue({ status: 'queued', clip_id: 'clip-1' })
  vi.spyOn(clipEditRoute, 'useParams').mockReturnValue({ clipId: 'clip-1' } as never)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Page = clipEditRoute.options.component as ComponentType
  render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50)
  })
}

async function clickRerender() {
  fireEvent.click(screen.getByRole('button', { name: /regenerate captions/i }))
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(api.listClips).mockReset()
  vi.mocked(api.rerenderClip).mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('ClipEditorPage — B-roll credits', () => {
  it("shows the clip's B-roll credits and the Pexels link", async () => {
    vi.mocked(api.listClips).mockResolvedValue([
      {
        clip_id: 'clip-1',
        job_id: 'job-1',
        title: 'Intro',
        output_path: null,
        broll_assets: [
          {
            query: 'ocean',
            start: 3,
            duration: 3,
            provider: 'pexels',
            asset_id: '1234',
            author: 'Jane Doe',
            source_url: 'https://www.pexels.com/video/ocean-1234/',
            path: '/var/cache/1234.mp4',
          },
        ],
      } as never,
    ])
    vi.spyOn(clipEditRoute, 'useParams').mockReturnValue({ clipId: 'clip-1' } as never)
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const Page = clipEditRoute.options.component as ComponentType
    render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })

    expect(screen.getByRole('heading', { name: 'B-roll credits' })).toBeTruthy()
    expect(screen.getByRole('link', { name: /Jane Doe/ }).getAttribute('href')).toBe(
      'https://www.pexels.com/video/ocean-1234/',
    )
    expect(screen.getByRole('link', { name: /Videos provided by Pexels/ })).toBeTruthy()
  })

  it('shows no credits for a clip without B-roll', async () => {
    await renderPage()

    expect(screen.queryByRole('heading', { name: 'B-roll credits' })).toBeNull()
  })
})

describe('ClipEditorPage — re-render copy option', () => {
  it('shows the checkbox ticked by default and sends regenerate_copy: true', async () => {
    await renderPage()

    const box = screen.getByRole('checkbox', { name: REGEN_LABEL })
    expect((box as HTMLInputElement).checked).toBe(true)

    await clickRerender()

    expect(api.rerenderClip).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.rerenderClip).mock.calls[0]).toEqual([
      'clip-1',
      { reframe_provider: 'letterbox', regenerate_copy: true },
    ])
  })

  it('sends regenerate_copy: false when the box is unticked', async () => {
    await renderPage()

    fireEvent.click(screen.getByRole('checkbox', { name: REGEN_LABEL }))
    await clickRerender()

    expect(vi.mocked(api.rerenderClip).mock.calls[0]).toEqual([
      'clip-1',
      { reframe_provider: 'letterbox', regenerate_copy: false },
    ])
  })
})
