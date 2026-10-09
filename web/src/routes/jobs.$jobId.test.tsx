import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'
import type { JobState } from '@/api/client'

vi.mock('@/api/client', () => ({
  api: {
    getJob: vi.fn(),
    listClips: vi.fn(),
  },
}))

import { api } from '@/api/client'
import { jobDetailRoute } from './jobs.$jobId'

class FakeEventSource {
  static instances: FakeEventSource[] = []
  closed = false
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  url: string
  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener() {}
  removeEventListener() {}
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
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50)
  })
  return qc
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.mocked(api.getJob).mockReset()
  vi.mocked(api.listClips).mockReset()
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
    const open = FakeEventSource.instances.filter((es) => !es.closed)
    expect(open.map((es) => es.url)).toEqual(['/api/jobs/job-1/events'])
  })

  it.each(['completed', 'failed'] as const)(
    'leaves no SSE stream open for a job that is already %s on load',
    async (status) => {
      await renderPage(status)
      expect(FakeEventSource.instances.filter((es) => !es.closed)).toEqual([])
    },
  )
})
