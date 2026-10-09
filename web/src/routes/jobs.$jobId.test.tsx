import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'
import type { JobState } from '@/api/client'

vi.mock('@/api/client', () => ({
  api: {
    getJob: vi.fn(),
    listClips: vi.fn(),
    repromptJob: vi.fn(),
  },
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

import { api } from '@/api/client'
import { toast } from 'sonner'
import { jobDetailRoute } from './jobs.$jobId'

type Listener = (e: MessageEvent) => void

/** jsdom has no EventSource; named events reach only addEventListener listeners. */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  closed = false
  onmessage: Listener | null = null
  onerror: (() => void) | null = null
  url: string
  private listeners: Record<string, Listener[]> = {}
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: Listener) {
    this.listeners[type] = [...(this.listeners[type] ?? []), fn]
  }
  removeEventListener(type: string, fn: Listener) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((l) => l !== fn)
  }
  /** A backend event (Event.to_dict()) sent as a named SSE event. */
  emit(type: string, id: string, payload: Record<string, unknown> = {}) {
    if (this.closed) return
    const data = JSON.stringify({ event_id: id, type, job_id: 'job-1', payload })
    const e = { type, data, lastEventId: id } as MessageEvent
    this.listeners[type]?.forEach((fn) => fn(e))
  }
  close() {
    this.closed = true
  }
}

function job(status: JobState['status']): JobState {
  return {
    job_id: 'job-1',
    status,
    current_step: status === 'completed' ? 'completed' : 'chapters',
    url: 'upload://sample.mp4',
    source: null,
    download_path: '/tmp',
    destination_folder: '/tmp/x',
    video_path: '/tmp/x/v.mp4',
    chapters: {},
    output_paths: [],
    error: null,
  } as unknown as JobState
}

async function renderPage(status: JobState['status']) {
  vi.mocked(api.getJob).mockResolvedValue(job(status))
  vi.mocked(api.listClips).mockResolvedValue([])
  vi.spyOn(jobDetailRoute, 'useParams').mockReturnValue({ jobId: 'job-1' } as never)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Page = jobDetailRoute.options.component as ComponentType
  render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
  await flush()
  return qc
}

async function flush(ms = 50) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function openStreams() {
  return FakeEventSource.instances.filter((es) => !es.closed)
}

function submitButton() {
  return screen.getByRole('button', { name: /^reprompt/i }) as HTMLButtonElement
}

async function submitReprompt(prompt = 'gender equality') {
  fireEvent.change(screen.getByLabelText(/new prompt/i), { target: { value: prompt } })
  fireEvent.click(submitButton())
  await flush()
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.mocked(api.getJob).mockReset()
  vi.mocked(api.listClips).mockReset()
  vi.mocked(api.repromptJob).mockReset()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('JobDetailPage — progress comes from SSE, not polling', () => {
  it('does not poll the job or its clips while the job is running', async () => {
    await renderPage('running')
    expect(api.getJob).toHaveBeenCalledTimes(1)
    expect(api.listClips).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000)
    })
    expect(api.getJob).toHaveBeenCalledTimes(1)
    expect(api.listClips).toHaveBeenCalledTimes(1)
  })

  it('opens the job SSE stream for a running job', async () => {
    await renderPage('running')
    expect(openStreams().map((es) => es.url)).toEqual(['/api/jobs/job-1/events'])
  })

  it.each(['completed', 'failed'] as const)(
    'leaves no SSE stream open for a job that is already %s on load',
    async (status) => {
      await renderPage(status)
      expect(openStreams()).toEqual([])
    },
  )
})

describe('JobDetailPage — reprompt', () => {
  it('offers the reprompt form only on a completed job', async () => {
    await renderPage('completed')
    expect(screen.getByLabelText(/new prompt/i)).toBeInTheDocument()
  })

  it.each(['pending', 'running', 'failed'] as const)('hides the reprompt form on a %s job', async (status) => {
    await renderPage(status)
    expect(screen.queryByLabelText(/new prompt/i)).toBeNull()
  })

  it('sends the prompt and the clip length range', async () => {
    vi.mocked(api.repromptJob).mockResolvedValue({
      job_id: 'job-1',
      status: 'queued',
      prompt: 'gender equality',
      pipeline_options: {},
    })
    await renderPage('completed')

    fireEvent.change(screen.getByLabelText(/min length/i), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText(/max length/i), { target: { value: '45' } })
    await submitReprompt('  gender equality  ')

    expect(vi.mocked(api.repromptJob).mock.calls).toEqual([
      ['job-1', { prompt: 'gender equality', length_min_seconds: 20, length_max_seconds: 45 }],
    ])
  })

  it('leaves out the lengths that are blank', async () => {
    vi.mocked(api.repromptJob).mockResolvedValue({
      job_id: 'job-1',
      status: 'queued',
      prompt: 'x',
      pipeline_options: {},
    })
    await renderPage('completed')

    await submitReprompt('x')

    expect(vi.mocked(api.repromptJob).mock.calls[0]).toEqual(['job-1', { prompt: 'x' }])
  })

  it("shows the server's reason when the reprompt is refused (409)", async () => {
    vi.mocked(api.repromptJob).mockRejectedValue(
      new Error('409 {"detail":"a reprompt of this job is already running"}'),
    )
    await renderPage('completed')

    await submitReprompt()

    expect(toast.error).toHaveBeenCalledWith('Cannot reprompt: a reprompt of this job is already running')
    expect(openStreams()).toEqual([])
    expect(submitButton().disabled).toBe(false)
  })

  it('disables the button while the request is pending', async () => {
    vi.mocked(api.repromptJob).mockReturnValue(new Promise(() => {}))
    await renderPage('completed')

    await submitReprompt()

    expect(submitButton().disabled).toBe(true)
  })

  it('follows the queued reprompt over SSE until the new clips are in', async () => {
    vi.mocked(api.repromptJob).mockResolvedValue({
      job_id: 'job-1',
      status: 'queued',
      prompt: 'gender equality',
      pipeline_options: {},
    })
    await renderPage('completed')
    const clipFetches = vi.mocked(api.listClips).mock.calls.length

    await submitReprompt()

    const [stream] = openStreams()
    expect(stream.url).toBe('/api/jobs/job-1/events')
    expect(submitButton().disabled).toBe(true)

    act(() => {
      stream.emit('ClipRendered', 'ev-1', { chapter_index: 2 })
      stream.emit('JobReprompted', 'ev-2', { clip_ids: ['n1'] })
      stream.emit('JobCompleted', 'ev-3')
    })
    await flush(600)

    expect(stream.closed).toBe(true)
    expect(toast.success).toHaveBeenCalledWith('New clips are ready')
    expect(vi.mocked(api.listClips).mock.calls.length).toBeGreaterThan(clipFetches)
    expect(submitButton().disabled).toBe(false)
    expect(openStreams()).toEqual([])
  })

  it("does not take the original run's JobCompleted for the reprompt's outcome", async () => {
    vi.mocked(api.repromptJob).mockResolvedValue({
      job_id: 'job-1',
      status: 'queued',
      prompt: 'gender equality',
      pipeline_options: {},
    })
    const qc = await renderPage('completed')
    // Events cached while this page watched the original run.
    qc.setQueryData(['job-events', 'job-1'], [{ id: 'old-done', type: 'JobCompleted' }])

    await submitReprompt()

    expect(openStreams().map((es) => es.url)).toEqual(['/api/jobs/job-1/events'])
    expect(submitButton().disabled).toBe(true)
    expect(toast.success).not.toHaveBeenCalledWith('New clips are ready')
  })

  it('reports a failed reprompt and keeps the clips', async () => {
    vi.mocked(api.repromptJob).mockResolvedValue({
      job_id: 'job-1',
      status: 'queued',
      prompt: 'gender equality',
      pipeline_options: {},
    })
    await renderPage('completed')
    await submitReprompt()
    const [stream] = openStreams()

    act(() => {
      stream.emit('RepromptFailed', 'ev-9', { error: 'render exploded' })
    })
    await flush()

    expect(stream.closed).toBe(true)
    expect(toast.error).toHaveBeenCalledWith('Reprompt failed: render exploded')
    expect(submitButton().disabled).toBe(false)
    expect(openStreams()).toEqual([])
  })
})
