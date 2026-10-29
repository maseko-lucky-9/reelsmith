import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'

vi.mock('@/api/client', () => ({
  api: { createJob: vi.fn() },
}))

vi.mock('sonner', () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }))

vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => vi.fn(),
}))

import { toast } from 'sonner'
import { api } from '@/api/client'
import { PROMPT_MAX_LENGTH } from '@/components/scene-prompt-field'
import { jobsNewRoute } from './jobs.new'

const URL = 'https://www.youtube.com/watch?v=xxxxxxxxxxx'

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const Page = jobsNewRoute.options.component as ComponentType
  render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
}

beforeEach(() => {
  vi.mocked(toast.success).mockClear()
  vi.mocked(toast.info).mockClear()
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

describe('NewJobPage — prompt (spec 014)', () => {
  it('sends the prompt the user typed, trimmed', async () => {
    renderPage()
    fireEvent.change(screen.getByPlaceholderText(/YouTube, Facebook/), { target: { value: URL } })
    fireEvent.change(screen.getByLabelText('Find moments (optional)'), {
      target: { value: '  the moment the speaker gets emotional ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Start job' }))

    await waitFor(() => expect(api.createJob).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.createJob).mock.calls[0][0].prompt).toBe('the moment the speaker gets emotional')
  })

  it('caps the prompt at the API limit of 2000 characters', () => {
    renderPage()
    const box = screen.getByLabelText('Find moments (optional)') as HTMLTextAreaElement
    expect(PROMPT_MAX_LENGTH).toBe(2000)
    expect(box.maxLength).toBe(2000)
  })

  it('says a new job was queued only when one was', async () => {
    renderPage()
    fireEvent.change(screen.getByPlaceholderText(/YouTube, Facebook/), { target: { value: URL } })
    fireEvent.click(screen.getByRole('button', { name: 'Start job' }))
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Job job-1 queued'))
    expect(toast.info).not.toHaveBeenCalled()
  })

  it('says so when the link already has a job', async () => {
    vi.mocked(api.createJob).mockResolvedValue({ job_id: 'old-job', status: 'completed' })
    renderPage()
    fireEvent.change(screen.getByPlaceholderText(/YouTube, Facebook/), { target: { value: URL } })
    fireEvent.click(screen.getByRole('button', { name: 'Start job' }))
    await waitFor(() => expect(toast.info).toHaveBeenCalledTimes(1))
    expect(vi.mocked(toast.info).mock.calls[0][0]).toBe('This link already has job old-job (completed); opening it')
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('omits a blank prompt', async () => {
    renderPage()
    fireEvent.change(screen.getByPlaceholderText(/YouTube, Facebook/), { target: { value: URL } })
    fireEvent.change(screen.getByLabelText('Find moments (optional)'), { target: { value: '  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Start job' }))

    await waitFor(() => expect(api.createJob).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.createJob).mock.calls[0][0]).not.toHaveProperty('prompt')
  })
})
