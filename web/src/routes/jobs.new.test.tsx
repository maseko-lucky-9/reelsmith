import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'

vi.mock('@/api/client', () => ({
  api: { createJob: vi.fn() },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => vi.fn(),
}))

import { api } from '@/api/client'
import { jobsNewRoute } from './jobs.new'

const URL = 'https://www.youtube.com/watch?v=xxxxxxxxxxx'

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const Page = jobsNewRoute.options.component as ComponentType
  render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
}

beforeEach(() => {
  vi.mocked(api.createJob).mockReset()
  vi.mocked(api.createJob).mockResolvedValue({ job_id: 'job-1', status: 'accepted' })
})

describe('NewJobPage — download path (T035)', () => {
  it('starts empty and leaves the path to the server', async () => {
    renderPage()
    const pathInput = screen.getByLabelText('Download path') as HTMLInputElement
    expect(pathInput.value).toBe('')

    fireEvent.change(screen.getByPlaceholderText(/YouTube, Facebook/), {
      target: { value: URL },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Start job' }))

    await waitFor(() => expect(api.createJob).toHaveBeenCalledTimes(1))
    const req = vi.mocked(api.createJob).mock.calls[0][0]
    expect(req.url).toBe(URL)
    expect(req).not.toHaveProperty('download_path')
  })

  it('sends a path the user typed', async () => {
    renderPage()
    fireEvent.change(screen.getByPlaceholderText(/YouTube, Facebook/), {
      target: { value: URL },
    })
    fireEvent.change(screen.getByLabelText('Download path'), {
      target: { value: '  /data/reels  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Start job' }))

    await waitFor(() => expect(api.createJob).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.createJob).mock.calls[0][0].download_path).toBe('/data/reels')
  })
})
