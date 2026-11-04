/**
 * apiErrorDetail / apiErrorStatus on errors thrown by the real API client
 * (apiFetch), with only `fetch` stubbed, so the error format they parse is
 * the one the client actually produces.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { api } from '@/api/client'
import { apiErrorDetail, apiErrorStatus } from './apiErrorDetail'

async function errorFrom(body: string, status: number): Promise<unknown> {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status })))
  try {
    await api.getClip('clip-1')
  } catch (e) {
    return e
  }
  throw new Error('expected the request to fail')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiErrorDetail / apiErrorStatus with the real client', () => {
  it('reads the status and no detail from a plain-text body', async () => {
    const err = await errorFrom('Internal Server Error', 500)
    expect(apiErrorStatus(err)).toBe(500)
    expect(apiErrorDetail(err)).toBeNull()
  })

  it('reads the status and no detail from an HTML proxy page', async () => {
    const err = await errorFrom('<html><body>502 Bad Gateway</body></html>', 502)
    expect(apiErrorStatus(err)).toBe(502)
    expect(apiErrorDetail(err)).toBeNull()
  })

  it('reads the status from an empty body', async () => {
    const err = await errorFrom('', 503)
    expect(apiErrorStatus(err)).toBe(503)
    expect(apiErrorDetail(err)).toBeNull()
  })

  it('reads both from a FastAPI JSON error', async () => {
    const err = await errorFrom(JSON.stringify({ detail: 'clip not found' }), 404)
    expect(apiErrorStatus(err)).toBe(404)
    expect(apiErrorDetail(err)).toBe('clip not found')
  })
})
