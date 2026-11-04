/**
 * Clip editor review fixes (spec 016 Phase 4a): Save/Re-render locked while a
 * voice-over is made, its 409/503/422/404 messages, the stub-voice warning,
 * and the playhead guard while the segments are not rendered yet. Real
 * useTimelineEditor hook against a mocked API.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'
import type { ClipEditState, TimelinePayload, VoiceoverResponse } from '@/api/client'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return { ...actual, useNavigate: () => vi.fn() }
})

vi.mock('@/api/client', () => ({
  api: {
    listClips: vi.fn(),
    rerenderClip: vi.fn(),
    getClipEdit: vi.fn(),
    upsertClipEdit: vi.fn(),
    createVoiceover: vi.fn(),
  },
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}))

vi.mock('@/components/editor/MultiTrackTimeline', () => ({
  MultiTrackTimeline: () => null,
}))

import { api } from '@/api/client'
import { toast } from 'sonner'
import { clipEditRoute } from './clips.$clipId.edit'

/** A saved v2 edit of a clip at 100-110 s of its source. */
const SAVED: TimelinePayload = {
  schema_version: 2,
  tracks: [
    { kind: 'video', items: [{ start: 100, end: 110, src: 'main' }] },
    {
      kind: 'caption',
      items: [
        { start: 1, end: 2, text: 'hello there', words: [] },
        { start: 5, end: 6, text: 'later on', words: [] },
      ],
    },
    { kind: 'text-overlay', items: [] },
  ],
}

const edit = (timeline: TimelinePayload, version: number): ClipEditState => ({
  clip_id: 'clip-1',
  timeline,
  version,
  created_at: 'x',
  updated_at: 'x',
})

function voiceoverResponse(provider: string): VoiceoverResponse {
  const timeline: TimelinePayload = {
    ...structuredClone(SAVED),
    tracks: [...structuredClone(SAVED).tracks, { kind: 'audio', items: [{ start: 0, end: 2, src: 'vo.wav', gain: 1 }] }],
  }
  return { edit: edit(timeline, 2), item: { start: 0, end: 2, src: 'vo.wav', gain: 1 }, provider }
}

async function renderPage(clip: Record<string, unknown> = {}) {
  vi.mocked(api.listClips).mockResolvedValue([
    {
      clip_id: 'clip-1',
      job_id: 'job-1',
      title: 'Intro',
      output_path: null,
      start: 100,
      end: 110,
      ai_hook_text: 'Wait for the twist',
      ...clip,
    } as never,
  ])
  vi.spyOn(clipEditRoute, 'useParams').mockReturnValue({ clipId: 'clip-1' } as never)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const Page = clipEditRoute.options.component as ComponentType
  const view = render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
  await screen.findByRole('textbox', { name: 'Caption 1' })
  return view
}

function editCaption(n: number, value: string) {
  const box = screen.getByRole('textbox', { name: `Caption ${n}` })
  fireEvent.change(box, { target: { value } })
  fireEvent.blur(box)
}

function generateVoiceover() {
  fireEvent.click(screen.getByRole('button', { name: 'Voice-over' }))
  fireEvent.click(screen.getByRole('button', { name: 'Generate voice-over' }))
}

const button = (name: string | RegExp) => screen.getByRole('button', { name }) as HTMLButtonElement

beforeEach(() => {
  vi.mocked(api.getClipEdit).mockReset().mockResolvedValue(edit(structuredClone(SAVED), 1))
  vi.mocked(api.upsertClipEdit)
    .mockReset()
    .mockImplementation(async (_id, body) => edit(body.timeline, (body.version ?? 0) + 1))
  vi.mocked(api.rerenderClip).mockReset().mockResolvedValue({ status: 'queued', clip_id: 'clip-1' })
  vi.mocked(api.createVoiceover).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.warning).mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('voice-over in flight', () => {
  it('disables Save and Re-render until it answers', async () => {
    let answer!: (r: VoiceoverResponse) => void
    vi.mocked(api.createVoiceover).mockReturnValue(new Promise((r) => (answer = r)))
    await renderPage()

    generateVoiceover()
    await waitFor(() => expect(api.createVoiceover).toHaveBeenCalled())
    editCaption(1, 'edited meanwhile')

    expect(button('Save changes').disabled).toBe(true)
    expect(button('Re-render').disabled).toBe(true)
    expect(button(/regenerate captions/i).disabled).toBe(true)

    await act(async () => answer(voiceoverResponse('piper')))
    await waitFor(() => expect(button('Re-render').disabled).toBe(false))
  })
})

describe('voice-over messages', () => {
  it.each([
    [
      409,
      "the clip's edit changed while the voice-over was being made; reload the edit and try again",
      /edit changed while the voice-over was being generated.*Reload the page and try again/,
    ],
    [503, 'voice-over busy; try again shortly', /busy making other voice-overs\. Try again in a moment/],
    [422, "start 20 is at or past the reel's end (10.000)", /^Voice-over failed \(422\): start 20 is at or past the reel's end/],
    [404, 'clip has no AI hook text; send text', /^Voice-over failed \(404\): clip has no AI hook text; send text$/],
  ])('shows a %i distinctly', async (status, detail, shown) => {
    vi.mocked(api.createVoiceover).mockRejectedValue(new Error(`${status} ${JSON.stringify({ detail })}`))
    await renderPage()

    generateVoiceover()

    expect((await screen.findByRole('alert')).textContent).toMatch(shown)
  })

  it('warns that a stub voice-over is a silent placeholder', async () => {
    vi.mocked(api.createVoiceover).mockResolvedValue(voiceoverResponse('stub'))
    await renderPage()

    generateVoiceover()

    await waitFor(() => expect(toast.warning).toHaveBeenCalledTimes(1))
    const message = String(vi.mocked(toast.warning).mock.calls[0][0])
    expect(message).toMatch(/silent placeholder/)
    expect(message).toMatch(/no Piper voice is installed/)
    expect(message).toContain('python -m scripts.fetch_piper_voice')
    expect(toast.success).not.toHaveBeenCalledWith('Voice-over added')
  })

  it('confirms a Piper voice-over', async () => {
    vi.mocked(api.createVoiceover).mockResolvedValue(voiceoverResponse('piper'))
    await renderPage()

    generateVoiceover()

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Voice-over added'))
    expect(toast.warning).not.toHaveBeenCalled()
  })
})

describe('playhead while the segments are not rendered', () => {
  async function renderWithPlayhead(seconds: number) {
    const view = await renderPage({ output_path: '/data/clip-1.mp4' })
    const video = view.container.querySelector('video') as HTMLVideoElement
    Object.defineProperty(video, 'currentTime', { configurable: true, value: seconds, writable: true })
    fireEvent(video, new Event('timeupdate'))
    fireEvent.click(screen.getByRole('button', { name: 'Trim' }))
  }

  function trimStart(value: string) {
    const start = screen.getByRole('spinbutton', { name: 'Segment 1 start' })
    fireEvent.change(start, { target: { value } })
    fireEvent.blur(start)
  }

  it('disables Split at playhead with a reason after a trim, until it is re-rendered', async () => {
    await renderWithPlayhead(4)
    expect(button('Split at playhead').disabled).toBe(false)
    expect(button('Split at playhead').title).toBe('')

    trimStart('101.5')

    expect(button('Split at playhead').disabled).toBe(true)
    expect(button('Split at playhead').title).toMatch(/Re-render first/)

    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(api.rerenderClip).toHaveBeenCalled())
    await waitFor(() => expect(button('Split at playhead').disabled).toBe(false))
  })

  it('does not count a caption edit as an unrendered segment change', async () => {
    await renderWithPlayhead(4)
    editCaption(1, 'new words')
    expect(button('Split at playhead').disabled).toBe(false)
  })

  it('does not highlight a caption from the stale playhead after a trim', async () => {
    await renderWithPlayhead(4)
    const row = () => screen.getByRole('textbox', { name: 'Caption 2' }).closest('li') as HTMLElement
    expect(row().className).not.toContain('bg-white/10')

    // Caption 2 moves to 3.5-4.5 on the edited reel; the file's 4 s is not that.
    trimStart('101.5')

    expect(row().className).not.toContain('bg-white/10')
  })
})
