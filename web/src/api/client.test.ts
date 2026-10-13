import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from './client'

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset().mockResolvedValue({ ok: true, json: async () => [] })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function requestedQuery(): URLSearchParams {
  const url = String(fetchMock.mock.calls[0][0])
  return new URL(url, 'http://localhost').searchParams
}

describe('api.listJobs', () => {
  // The API defaults to 20 jobs, which hid every older job from the home page
  // and the badge; the history is the point of persisting jobs.
  it('asks for the whole history by default, not the API default of 20', async () => {
    await api.listJobs()

    expect(requestedQuery().get('limit')).toBe('500')
  })

  it('lets a caller ask for a smaller page', async () => {
    await api.listJobs({ limit: 5, offset: 10, search: 'rice' })

    const q = requestedQuery()
    expect([q.get('limit'), q.get('offset'), q.get('search')]).toEqual(['5', '10', 'rice'])
  })
})
