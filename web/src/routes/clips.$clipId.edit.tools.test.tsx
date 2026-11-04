/**
 * The clip editor's wired tools (spec 016 item 6): transcript caption edits,
 * caption style, text overlays, voice-over, trim and Re-render, through the
 * real useTimelineEditor hook against a mocked API.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'
import type { ClipEditState, ClipEditUpsert, TimelinePayload } from '@/api/client'

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

vi.mock('@/components/editor/MultiTrackTimeline', () => ({
  MultiTrackTimeline: () => null,
}))

import { api } from '@/api/client'
import { clipEditRoute } from './clips.$clipId.edit'

/** The server's default (v1) timeline for a clip at 100-110 s of its source. */
const DEFAULT_TIMELINE: TimelinePayload = {
  tracks: [
    { kind: 'video', items: [{ start: 0, end: 10, src: 'main', trim_start: 0 }] },
    {
      kind: 'caption',
      items: [
        {
          start: 1,
          end: 2,
          text: 'hello there',
          style: 'default',
          words: [
            { word: 'hello', start: 1, end: 1.5 },
            { word: 'there', start: 1.5, end: 2 },
          ],
        },
        { start: 5, end: 6, text: 'later on', style: 'default', words: [] },
      ],
    },
    { kind: 'text-overlay', items: [] },
  ],
}

function defaultEdit(): ClipEditState {
  return {
    clip_id: 'clip-1',
    timeline: structuredClone(DEFAULT_TIMELINE),
    version: 0,
    created_at: null,
    updated_at: null,
    is_default: true,
    words: [
      { word: 'hello', start: 1, end: 1.5 },
      { word: 'there', start: 1.5, end: 2 },
    ],
  }
}

function savedEdit(timeline: TimelinePayload, version: number): ClipEditState {
  return { clip_id: 'clip-1', timeline, version, created_at: 'x', updated_at: 'x' }
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
  render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
  await screen.findByRole('toolbar', { name: 'Editor tools' })
  await screen.findByRole('textbox', { name: 'Caption 1' })
}

function lastSaved(): ClipEditUpsert {
  const calls = vi.mocked(api.upsertClipEdit).mock.calls
  return calls[calls.length - 1][1]
}

const items = (tl: TimelinePayload, kind: string) => tl.tracks.find((t) => t.kind === kind)?.items ?? []

async function save() {
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
  await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalled())
}

beforeEach(() => {
  vi.mocked(api.getClipEdit).mockReset().mockResolvedValue(defaultEdit())
  vi.mocked(api.upsertClipEdit)
    .mockReset()
    .mockImplementation(async (_id, body) => savedEdit(body.timeline, (body.version ?? 0) + 1))
  vi.mocked(api.rerenderClip).mockReset().mockResolvedValue({ status: 'queued', clip_id: 'clip-1' })
  vi.mocked(api.createVoiceover).mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ClipEditorPage — tool rail', () => {
  it('offers only the wired tools; the dead icons are gone', async () => {
    await renderPage()

    const rail = screen.getByRole('toolbar', { name: 'Editor tools' })
    expect(within(rail).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Captions',
      'Text',
      'Voice-over',
      'Trim',
    ])
    for (const dead of ['AI enhance', 'Media', 'Brand template', 'B-Roll', 'Transitions', 'Music', 'AI hook']) {
      expect(screen.queryByTitle(dead)).toBeNull()
    }
  })
})

describe('ClipEditorPage — transcript', () => {
  it('edits a caption and saves it as schema v2 in source seconds', async () => {
    await renderPage()

    const box = screen.getByRole('textbox', { name: 'Caption 1' })
    fireEvent.change(box, { target: { value: 'hullo there' } })
    fireEvent.blur(box)
    await save()

    const { timeline, version } = lastSaved()
    expect(version).toBeUndefined() // first save of the default timeline
    expect(timeline.schema_version).toBe(2)
    expect(items(timeline, 'video')).toEqual([{ start: 100, end: 110, src: 'main' }])
    const caption = items(timeline, 'caption')[0]
    expect(caption.text).toBe('hullo there')
    expect(caption.words).toHaveLength(2)
    expect(await screen.findByRole('button', { name: 'Saved' })).toBeTruthy()
  })
})

describe('ClipEditorPage — captions tool', () => {
  it('drives caption_style from the template picker, with no "Not applied" badge', async () => {
    await renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Captions' }))
    expect(screen.getByTestId('caption-template-picker')).toBeTruthy()
    expect(screen.queryByText('Not applied to renders yet')).toBeNull()
    fireEvent.click(screen.getByTestId('caption-template-hormozi'))
    expect(screen.getByTestId('caption-template-hormozi').dataset.selected).toBe('true')
    await save()

    expect(lastSaved().timeline.caption_style).toBe('hormozi')
  })
})

describe('ClipEditorPage — text tool', () => {
  it('adds a text overlay to the timeline', async () => {
    await renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Text' }))
    const panel = screen.getByRole('region', { name: 'Text' })
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Text' }), { target: { value: 'Big reveal' } })
    fireEvent.change(within(panel).getByRole('spinbutton', { name: 'Start (s)' }), { target: { value: '2' } })
    fireEvent.click(within(panel).getByRole('button', { name: 'Add text' }))
    await save()

    expect(items(lastSaved().timeline, 'text-overlay')).toEqual([
      { start: 2, end: 5, text: 'Big reveal', x: 0.5, y: 0.1 },
    ])
  })
})

describe('ClipEditorPage — trim tool', () => {
  it('moves the first segment start and shifts the captions with it', async () => {
    await renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Trim' }))
    const start = screen.getByRole('spinbutton', { name: 'Segment 1 start' })
    fireEvent.change(start, { target: { value: '101.5' } })
    fireEvent.blur(start)
    await save()

    const { timeline } = lastSaved()
    expect(items(timeline, 'video')).toEqual([{ start: 101.5, end: 110, src: 'main' }])
    expect(items(timeline, 'caption').map((c) => [c.start, c.end, c.text])).toEqual([
      [0, 0.5, 'there'],
      [3.5, 4.5, 'later on'],
    ])
  })

  it('refuses a segment shorter than the minimum', async () => {
    await renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Trim' }))
    const end = screen.getByRole('spinbutton', { name: 'Segment 1 end' })
    fireEvent.change(end, { target: { value: '100' } })
    fireEvent.blur(end)

    expect(screen.getByRole('alert').textContent).toMatch(/at least 0.05s/)
    expect(screen.getByRole('button', { name: 'Saved' })).toBeTruthy()
  })
})

describe('ClipEditorPage — voice-over tool', () => {
  it("defaults the script to the clip's AI hook and adopts the server's edit", async () => {
    const withAudio: TimelinePayload = {
      schema_version: 2,
      tracks: [
        { kind: 'video', items: [{ start: 100, end: 110, src: 'main' }] },
        { kind: 'audio', items: [{ start: 0, end: 2.4, src: 'voiceover_1.wav', gain: 1 }] },
      ],
    }
    vi.mocked(api.createVoiceover).mockResolvedValue({
      edit: savedEdit(withAudio, 1),
      item: { start: 0, end: 2.4, src: 'voiceover_1.wav', gain: 1 },
      provider: 'piper',
    })
    await renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Voice-over' }))
    const script = screen.getByRole('textbox', { name: 'Script' }) as HTMLTextAreaElement
    expect(script.value).toBe('Wait for the twist')
    fireEvent.click(screen.getByRole('button', { name: 'Generate voice-over' }))

    await waitFor(() => expect(api.createVoiceover).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.createVoiceover).mock.calls[0]).toEqual(['clip-1', { text: 'Wait for the twist', start: 0 }])
    // Nothing local to save: the server builds the v2 edit from the default.
    expect(api.upsertClipEdit).not.toHaveBeenCalled()
    expect(await screen.findByRole('list', { name: 'Voice-overs' })).toBeTruthy()
    expect(screen.getByText('voiceover_1.wav')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Saved' })).toBeTruthy()
  })

  it('leaves the script empty without an AI hook', async () => {
    await renderPage({ ai_hook_text: null })

    fireEvent.click(screen.getByRole('button', { name: 'Voice-over' }))
    expect((screen.getByRole('textbox', { name: 'Script' }) as HTMLTextAreaElement).value).toBe('')
    expect((screen.getByRole('button', { name: 'Generate voice-over' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it.each([
    [503, 'voice-over is off (YTVIDEO_VOICEOVER_PROVIDER=off)', /unavailable on the server \(503\): voice-over is off/],
    [502, 'piper exited 1', /voice engine failed \(502\): piper exited 1/],
    [409, "the clip's job is busy; try again when it is done", /Cannot add a voice-over \(409\): the clip's job is busy/],
  ])('shows a %i error', async (status, detail, shown) => {
    vi.mocked(api.createVoiceover).mockRejectedValue(new Error(`${status} ${JSON.stringify({ detail })}`))
    await renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Voice-over' }))
    fireEvent.click(screen.getByRole('button', { name: 'Generate voice-over' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(shown)
  })

  it('saves a saved v1 edit as v2 before asking for a voice-over', async () => {
    vi.mocked(api.getClipEdit).mockResolvedValue(savedEdit(structuredClone(DEFAULT_TIMELINE), 3))
    vi.mocked(api.createVoiceover).mockRejectedValue(new Error('502 {"detail":"x"}'))
    await renderPage()

    fireEvent.click(screen.getByRole('button', { name: 'Voice-over' }))
    fireEvent.click(screen.getByRole('button', { name: 'Generate voice-over' }))

    await waitFor(() => expect(api.createVoiceover).toHaveBeenCalled())
    expect(lastSaved()).toMatchObject({ version: 3, timeline: { schema_version: 2 } })
    expect(vi.mocked(api.upsertClipEdit).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.createVoiceover).mock.invocationCallOrder[0],
    )
  })
})

describe('ClipEditorPage — Re-render', () => {
  it('saves pending edits, then queues the re-render', async () => {
    await renderPage()

    const box = screen.getByRole('textbox', { name: 'Caption 2' })
    fireEvent.change(box, { target: { value: 'much later' } })
    fireEvent.blur(box)
    fireEvent.click(screen.getByRole('button', { name: 'Re-render' }))

    await waitFor(() => expect(api.rerenderClip).toHaveBeenCalledTimes(1))
    expect(items(lastSaved().timeline, 'caption')[1].text).toBe('much later')
    expect(vi.mocked(api.upsertClipEdit).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(api.rerenderClip).mock.invocationCallOrder[0],
    )
  })

  it('does not queue the re-render when the save fails', async () => {
    vi.mocked(api.upsertClipEdit).mockRejectedValue(new Error('422 {"detail":"bad"}'))
    await renderPage()

    const box = screen.getByRole('textbox', { name: 'Caption 1' })
    fireEvent.change(box, { target: { value: 'x y' } })
    fireEvent.blur(box)
    fireEvent.click(screen.getByRole('button', { name: 'Re-render' }))

    await waitFor(() => expect(api.upsertClipEdit).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 20))
    expect(api.rerenderClip).not.toHaveBeenCalled()
  })
})
