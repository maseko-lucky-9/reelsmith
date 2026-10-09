import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'

const URL = 'https://www.youtube.com/watch?v=xxxxxxxxxxx'

vi.mock('@/api/client', () => ({
  api: {
    createJob: vi.fn(),
    previewVideo: vi.fn(),
    listBrandTemplates: vi.fn(),
  },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => vi.fn(),
  useSearch: () => ({ url: encodeURIComponent(URL) }),
}))

import { api } from '@/api/client'
import { WorkflowPage } from './workflow'

beforeEach(() => {
  vi.mocked(api.createJob).mockReset()
  vi.mocked(api.createJob).mockResolvedValue({ job_id: 'job-1', status: 'accepted' })
  vi.mocked(api.previewVideo).mockResolvedValue({
    title: 'T',
    duration: 60,
    resolution: '1080p',
    thumbnail: '',
  })
  vi.mocked(api.listBrandTemplates).mockResolvedValue([])
})

describe('WorkflowPage submit (T035)', () => {
  it('creates the job without a download path so the server default applies', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(createElement(QueryClientProvider, { client: qc }, createElement(WorkflowPage)))

    fireEvent.click(screen.getByRole('button', { name: 'Get clips in 1 click' }))

    await waitFor(() => expect(api.createJob).toHaveBeenCalledTimes(1))
    const req = vi.mocked(api.createJob).mock.calls[0][0]
    expect(req.url).toBe(URL)
    expect(req).not.toHaveProperty('download_path')
  })
})
