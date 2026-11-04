/**
 * Clip editor review fixes (spec 016 Phase 4a): Save/Re-render locked while a
 * voice-over is made, its 409/503/422/404 messages, the stub-voice warning,
 * and the playhead guard while the segments are not rendered yet. Real
 * useClipEdit hook against a mocked API. The playhead cases run in the
 * rendered-clip view (spec 017 T155): the guard is for the rendered file's clock.
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

/** The rendered clip's clock: the view the 016 playhead guard is about. */
function showRenderedClip() {
  fireEvent.click(screen.getByRole('button', { name: 'Rendered clip' }))
}

function generateVoiceover() {
  fireEvent.click(screen.getByRole('button', { name: 'Voice-over' }))
  fireEvent.click(screen.getByRole('button', { name: 'Generate voice-over' }))
}

/** jsdom has no EventSource: records the editor's live stream and lets a test push named events. */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  static last() {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1]
  }
  listeners: Record<string, Array<(e: Event) => void>> = {}
  closed = false
  url: string
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: (e: Event) => void) {
    ;(this.listeners[type] ??= []).push(fn)
  }
  close() {
    this.closed = true
  }
  emit(type: string, payload: Record<string, unknown>) {
    const e = { type, data: JSON.stringify({ type, payload }) } as unknown as MessageEvent
    this.listeners[type]?.forEach((fn) => fn(e))
  }
}

const button = (name: string | RegExp) => screen.getByRole('button', { name }) as HTMLButtonElement

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.mocked(api.getClipEdit).mockReset().mockResolvedValue(edit(structuredClone(SAVED), 1))
  vi.mocked(api.upsertClipEdit)
    .mockReset()
    .mockImplementation(async (_id, body) => edit(body.timeline, (body.version ?? 0) + 1))
  vi.mocked(api.rerenderClip).mockReset().mockResolvedValue({ status: 'queued', clip_id: 'clip-1' })
  vi.mocked(api.createVoiceover).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.warning).mockReset()
  vi.mocked(toast.error).mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
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
    showRenderedClip()
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
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Re-render queued'))

    // 202 means queued: the preview still plays the old cut.
    expect(button('Split at playhead').disabled).toBe(true)
    const video = document.querySelector('video') as HTMLVideoElement
    const oldSrc = video.getAttribute('src')
    expect(oldSrc).toBe('/api/clips/clip-1/video')

    // Another clip's render does not count.
    act(() => FakeEventSource.last().emit('ClipRendered', { clip_id: 'other' }))
    expect(button('Split at playhead').disabled).toBe(true)

    act(() => FakeEventSource.last().emit('ClipRendered', { clip_id: 'clip-1', segments: [[101.5, 110]] }))
    await waitFor(() => expect(button('Split at playhead').disabled).toBe(false))
    const newSrc = (document.querySelector('video') as HTMLVideoElement).getAttribute('src')
    expect(newSrc).not.toBe(oldSrc)
    expect(newSrc).toMatch(/^\/api\/clips\/clip-1\/video\?v=\d+$/)
  })

  it('streams the live job events and closes the stream on unmount', async () => {
    const view = await renderPage({ output_path: '/data/clip-1.mp4' })
    expect(FakeEventSource.last().url).toContain('/api/jobs/job-1/events?live=true')
    expect(FakeEventSource.last().closed).toBe(false)
    view.unmount()
    expect(FakeEventSource.last().closed).toBe(true)
  })

  it('blocks a second Re-render and shows progress while one is in flight, until it ends', async () => {
    await renderWithPlayhead(4)
    trimStart('101.5')
    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Re-render queued'))

    expect(button('Re-render').disabled).toBe(true)
    expect(screen.getByRole('status').textContent).toContain('Re-render in progress')
    expect(button('Split at playhead').disabled).toBe(true)
    fireEvent.click(button('Re-render'))
    expect(api.rerenderClip).toHaveBeenCalledTimes(1)

    act(() => FakeEventSource.last().emit('ClipRendered', { clip_id: 'clip-1' }))
    await waitFor(() => expect(button('Re-render').disabled).toBe(false))
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('disables + Regenerate captions and Save while a re-render is in flight', async () => {
    await renderWithPlayhead(4)
    trimStart('101.5')
    expect(button('Save changes').disabled).toBe(false)
    expect(button(/regenerate captions/i).disabled).toBe(false)
    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Re-render queued'))

    expect(button(/regenerate captions/i).disabled).toBe(true)
    trimStart('102')
    expect(button('Save changes').disabled).toBe(true)
    fireEvent.click(button(/regenerate captions/i))
    expect(api.rerenderClip).toHaveBeenCalledTimes(1)

    fireEvent.click(button('Voice-over'))
    expect(button('Generate voice-over').disabled).toBe(true)
    expect(button('Generate voice-over').title).toMatch(/re-render/)
  })

  it('the cut in the ClipRendered event decides Split, not the edit at click time', async () => {
    await renderWithPlayhead(4)
    trimStart('101.5') // cut A
    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Re-render queued'))
    trimStart('102') // the edit moves on to cut B meanwhile

    // The render that finished is cut A: the file is A, the edit is B.
    act(() => FakeEventSource.last().emit('ClipRendered', { clip_id: 'clip-1', segments: [[101.5, 110]] }))
    await waitFor(() => expect(button('Re-render').disabled).toBe(false))
    expect(button('Split at playhead').disabled).toBe(true)

    fireEvent.click(button('Undo')) // back to cut A, the file's
    await waitFor(() => expect(button('Split at playhead').disabled).toBe(false))
  })

  it('an unknown cut ([]) in a ClipRendered event never enables Split', async () => {
    await renderWithPlayhead(4)
    trimStart('101.5')
    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Re-render queued'))

    act(() => FakeEventSource.last().emit('ClipRendered', { clip_id: 'clip-1', segments: [] }))

    await waitFor(() => expect(button('Re-render').disabled).toBe(false))
    expect(button('Split at playhead').disabled).toBe(true)
  })

  describe('reopening the editor', () => {
    async function reopen(rendered: unknown, trimmedStart = 101.5) {
      // The saved edit is trimmed to 101.5; the clip's file may or may not be.
      const trimmed = structuredClone(SAVED)
      trimmed.tracks[0].items[0] = { start: trimmedStart, end: 110, src: 'main' }
      vi.mocked(api.getClipEdit).mockResolvedValue(edit(trimmed, 2))
      const view = await renderPage({ output_path: '/data/clip-1.mp4', rendered_segments: rendered })
      showRenderedClip()
      const video = view.container.querySelector('video') as HTMLVideoElement
      Object.defineProperty(video, 'currentTime', { configurable: true, value: 4, writable: true })
      fireEvent(video, new Event('timeupdate'))
      fireEvent.click(screen.getByRole('button', { name: 'Trim' }))
    }

    it('keeps Split disabled when the saved cut was never rendered (a re-render still queued)', async () => {
      await reopen(null)
      expect(button('Split at playhead').disabled).toBe(true)
    })

    it('enables Split when the clip says its file was rendered from the saved cut', async () => {
      await reopen([[101.5, 110]])
      expect(button('Split at playhead').disabled).toBe(false)
    })

    it('treats boundaries within 1 ms as the same cut', async () => {
      await reopen([[101.5000004, 109.9999996]])
      expect(button('Split at playhead').disabled).toBe(false)
    })

    it('matches a raw float boundary and the server nudging it by 1 ms', async () => {
      await reopen([[101.0045, 110]], 101.0045)
      expect(button('Split at playhead').disabled).toBe(false)
    })

    it('matches a boundary the server nudged by 1 ms', async () => {
      await reopen([[101.0055, 110]], 101.0045)
      expect(button('Split at playhead').disabled).toBe(false)
    })

    it('does not match boundaries 1.1 ms apart', async () => {
      await reopen([[101.0056, 110]], 101.0045)
      expect(button('Split at playhead').disabled).toBe(true)
    })

    it('does not match a different number of segments', async () => {
      await reopen([[101.5, 110], [120, 130]])
      expect(button('Split at playhead').disabled).toBe(true)
    })

    it('never enables Split while the rendered cut is unknown ([]: a render is replacing the file)', async () => {
      // Even for an edit equal to the clip's own window.
      await reopen([], 100)
      expect(button('Split at playhead').disabled).toBe(true)
    })
  })

  it('unlocks Re-render after a failed re-render, keeping Split disabled', async () => {
    await renderWithPlayhead(4)
    trimStart('101.5')
    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(button('Re-render').disabled).toBe(true))

    act(() => FakeEventSource.last().emit('ClipRerenderFailed', { clip_id: 'clip-1', error: 'Re-render cancelled' }))

    await waitFor(() => expect(button('Re-render').disabled).toBe(false))
    expect(button('Split at playhead').disabled).toBe(true)
  })

  it('after a rejected re-render (409), a ClipRendered of the clip\'s own window leaves Split disabled for a trimmed edit', async () => {
    vi.mocked(api.rerenderClip).mockRejectedValue(new Error('409 {"detail":"job is running"}'))
    await renderWithPlayhead(4)
    trimStart('101.5')
    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(screen.queryByRole('status')).toBeNull()
    expect(button('Re-render').disabled).toBe(false)

    act(() => FakeEventSource.last().emit('ClipRendered', { clip_id: 'clip-1' }))

    expect(button('Split at playhead').disabled).toBe(true)
  })

  it('after a failed save (nothing queued), a ClipRendered of the clip\'s own window leaves Split disabled for a trimmed edit', async () => {
    vi.mocked(api.upsertClipEdit).mockRejectedValue(new Error('boom'))
    await renderWithPlayhead(4)
    trimStart('101.5')
    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(api.rerenderClip).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).toBeNull()

    act(() => FakeEventSource.last().emit('ClipRendered', { clip_id: 'clip-1' }))

    expect(button('Split at playhead').disabled).toBe(true)
  })

  it('keeps Split disabled and shows the error when the re-render fails', async () => {
    await renderWithPlayhead(4)
    trimStart('101.5')
    fireEvent.click(button('Re-render'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Re-render queued'))

    act(() => FakeEventSource.last().emit('ClipRerenderFailed', { clip_id: 'clip-1', error: 'ffmpeg exploded' }))

    expect((await screen.findByRole('alert')).textContent).toContain('ffmpeg exploded')
    expect(toast.error).toHaveBeenCalledWith('Re-render failed: ffmpeg exploded')
    expect(button('Split at playhead').disabled).toBe(true)
    expect(document.querySelector('video')?.getAttribute('src')).toBe('/api/clips/clip-1/video')
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
