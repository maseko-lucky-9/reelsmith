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

describe('api.uploadVideo (spec 014)', () => {
  it('posts the file and the prompt as multipart form fields', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ job_id: 'j1', upload_path: '/u', status: 'queued' }) })
    const file = new File([new Uint8Array(4)], 'talk.mp4', { type: 'video/mp4' })

    await api.uploadVideo(file, 'a red car')

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('/api/uploads')
    const form = init.body as FormData
    expect(form.get('prompt')).toBe('a red car')
    expect((form.get('file') as File).name).toBe('talk.mp4')
  })

  it('sends no prompt field without a prompt', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ job_id: 'j1', upload_path: '/u', status: 'queued' }) })
    await api.uploadVideo(new File([new Uint8Array(4)], 'talk.mp4'))
    expect((fetchMock.mock.calls[0][1].body as FormData).has('prompt')).toBe(false)
  })
})

describe('api.cancelJob / api.deleteJob (spec 013)', () => {
  it('cancels with a POST to the job cancel route', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 202, json: async () => ({ job_id: 'j1', status: 'cancelling' }) })

    const body = await api.cancelJob('j1')

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/jobs/j1/cancel')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'POST' })
    expect(body).toEqual({ job_id: 'j1', status: 'cancelling' })
  })

  it('deletes with a DELETE and resolves on 204 without reading a body', async () => {
    const json = vi.fn(async () => {
      throw new SyntaxError('Unexpected end of JSON input')
    })
    fetchMock.mockResolvedValue({ ok: true, status: 204, json })

    await expect(api.deleteJob('j1')).resolves.toBeUndefined()

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/jobs/j1')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'DELETE' })
    expect(json).not.toHaveBeenCalled()
  })

  it('rejects a 409 with the status and the detail', async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409, text: async () => '{"detail":"cancel it first"}' })

    await expect(api.deleteJob('j1')).rejects.toThrow('409 {"detail":"cancel it first"}')
  })
})
