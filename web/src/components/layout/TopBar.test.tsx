import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('@/api/client', () => ({ api: { listJobs: vi.fn() } }))

import { api } from '@/api/client'
import { TopBar } from './TopBar'

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(api.listJobs).mockReset().mockResolvedValue([])
})

afterEach(() => {
  vi.useRealTimers()
})

describe('TopBar — jobs badge polling', () => {
  it('refreshes the jobs list every 30s, not every 5s', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(createElement(QueryClientProvider, { client: qc }, createElement(TopBar)))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })
    expect(api.listJobs).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(29_000)
    })
    expect(api.listJobs).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500)
    })
    expect(api.listJobs).toHaveBeenCalledTimes(2)
  })
})
