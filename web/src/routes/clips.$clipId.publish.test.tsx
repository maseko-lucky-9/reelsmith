import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType, type ReactNode } from 'react'
import type { PublishJob } from '@/api/client'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useParams: () => ({ clipId: 'clip-1' }),
    Link: ({ children }: { children: ReactNode }) => createElement('a', null, children),
  }
})

vi.mock('@/api/client', () => ({
  api: {
    listSocialAccounts: vi.fn(),
    listPublishForClip: vi.fn(),
    getClip: vi.fn(),
    getPublish: vi.fn(),
    createPublish: vi.fn(),
  },
}))

import { api } from '@/api/client'
import { clipPublishRoute } from './clips.$clipId.publish'

function publishJob(status: PublishJob['status'], extra: Partial<PublishJob> = {}): PublishJob {
  return {
    id: `pj-${status}`,
    clip_id: 'clip-1',
    social_account_id: 'acc-1',
    title: 't',
    description: null,
    hashtags: [],
    status,
    posted_at: null,
    external_post_id: null,
    external_post_url: null,
    error: null,
    attempts: 0,
    created_at: '2026-10-09T00:00:00Z',
    ...extra,
  }
}

async function renderWithHistory(history: PublishJob[]) {
  vi.mocked(api.listPublishForClip).mockResolvedValue(history)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Page = clipPublishRoute.options.component as ComponentType
  render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50)
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_000)
  })
  return vi.mocked(api.listPublishForClip).mock.calls.length
}

async function renderAndSettle() {
  vi.mocked(api.listPublishForClip).mockResolvedValue([])
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Page = clipPublishRoute.options.component as ComponentType
  const view = render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
  await act(async () => {
    await vi.advanceTimersByTimeAsync(50)
  })
  return view
}

beforeEach(() => {
  vi.useFakeTimers({ now: new Date('2026-10-09T12:00:00Z') })
  vi.mocked(api.listSocialAccounts).mockReset().mockResolvedValue([])
  vi.mocked(api.getClip).mockReset().mockResolvedValue({ id: 'clip-1', title: 'c' } as never)
  vi.mocked(api.listPublishForClip).mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ClipPublishPage — history polling', () => {
  it('does not poll when every publish job is terminal', async () => {
    const calls = await renderWithHistory([
      publishJob('published'),
      publishJob('failed'),
      publishJob('posted_unverified'),
      publishJob('cancelled'),
    ])
    expect(calls).toBe(1)
  })

  it('does not poll with no publish history', async () => {
    expect(await renderWithHistory([])).toBe(1)
  })

  it('does not poll for a legacy pending job (nothing advances it since scheduling was dropped)', async () => {
    expect(await renderWithHistory([publishJob('pending')])).toBe(1)
  })

  it.each(['queued', 'posting'] as const)('polls while a publish is %s', async (status) => {
    const calls = await renderWithHistory([publishJob('published'), publishJob(status)])
    expect(calls).toBeGreaterThan(2)
  })
})

describe('ClipPublishPage — publish now only', () => {
  it('has no schedule input and submits without schedule_at', async () => {
    vi.mocked(api.listSocialAccounts).mockResolvedValue([
      { id: 'acc-1', platform: 'youtube', account_handle: '@me' } as never,
    ])
    vi.mocked(api.createPublish).mockReset().mockResolvedValue(publishJob('queued'))
    vi.mocked(api.getPublish).mockReset().mockResolvedValue(publishJob('queued'))
    const { container } = await renderAndSettle()

    expect(container.querySelector('input[type="datetime-local"]')).toBeNull()
    expect(screen.queryByText(/schedule/i)).toBeNull()

    fireEvent.change(container.querySelector('select')!, { target: { value: 'acc-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Publish now' }))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })

    expect(api.createPublish).toHaveBeenCalledTimes(1)
    expect(vi.mocked(api.createPublish).mock.calls[0][0]).not.toHaveProperty('schedule_at')
  })
})
