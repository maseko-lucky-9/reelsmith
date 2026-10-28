import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { JobState } from '@/api/client'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

vi.mock('@/api/client', () => ({
  api: {
    cancelJob: vi.fn(),
    deleteJob: vi.fn(),
  },
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

import { api } from '@/api/client'
import { toast } from 'sonner'
import { ProjectCard } from './ProjectCard'

function job(status: JobState['status'], overrides: Partial<JobState> = {}): JobState {
  return {
    job_id: 'job-1',
    status,
    title: 'Ocean talk',
    url: 'https://www.youtube.com/watch?v=x',
    output_paths: ['/a.mp4'],
    ...overrides,
  } as unknown as JobState
}

function renderCard(props: Parameters<typeof ProjectCard>[0]) {
  const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  const invalidate = vi.spyOn(qc, 'invalidateQueries')
  render(
    <QueryClientProvider client={qc}>
      <ProjectCard {...props} />
    </QueryClientProvider>,
  )
  return { invalidate }
}

/** Lets a mutation that was (wrongly) started reach its mutationFn. */
async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

let confirmAnswer = true

beforeEach(() => {
  confirmAnswer = true
  vi.stubGlobal('confirm', vi.fn(() => confirmAnswer))
  navigate.mockReset()
  vi.mocked(api.cancelJob).mockReset().mockResolvedValue({ job_id: 'job-1', status: 'cancelling' })
  vi.mocked(api.deleteJob).mockReset().mockResolvedValue(undefined)
  vi.mocked(toast.error).mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ProjectCard', () => {
  it.each(['pending', 'running'] as const)('offers Cancel, not delete, on a %s job', (status) => {
    renderCard({ job: job(status) })

    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete/i })).toBeNull()
  })

  it.each(['completed', 'failed', 'cancelled'] as const)('offers delete, not Cancel, on a %s job', (status) => {
    renderCard({ job: job(status) })

    expect(screen.getByRole('button', { name: /delete ocean talk/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^cancel$/i })).toBeNull()
  })

  it('shows the cancelled status on the badge', () => {
    renderCard({ job: job('cancelled') })

    expect(screen.getByText('cancelled')).toHaveClass('bg-amber-600')
  })

  it('cancels after a confirm, without opening the job, and refreshes the list', async () => {
    const { invalidate } = renderCard({ job: job('running') })

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

    await waitFor(() => expect(api.cancelJob).toHaveBeenCalledWith('job-1'))
    expect(navigate).not.toHaveBeenCalled()
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['jobs'] }))
  })

  it('does nothing when the cancel is not confirmed', async () => {
    confirmAnswer = false
    renderCard({ job: job('running') })

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    await settle()

    expect(api.cancelJob).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('deletes after a confirm, without opening the job', async () => {
    const { invalidate } = renderCard({ job: job('completed') })

    fireEvent.click(screen.getByRole('button', { name: /delete ocean talk/i }))

    await waitFor(() => expect(api.deleteJob).toHaveBeenCalledWith('job-1'))
    expect(navigate).not.toHaveBeenCalled()
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['jobs'] }))
  })

  it('does not delete when the delete is not confirmed', async () => {
    confirmAnswer = false
    renderCard({ job: job('completed') })

    fireEvent.click(screen.getByRole('button', { name: /delete ocean talk/i }))
    await settle()

    expect(api.deleteJob).not.toHaveBeenCalled()
  })

  it('reports a delete the server refused', async () => {
    vi.mocked(api.deleteJob).mockRejectedValue(new Error('409 cancel it first'))
    renderCard({ job: job('completed') })

    fireEvent.click(screen.getByRole('button', { name: /delete ocean talk/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not delete: 409 cancel it first'))
  })

  it('opens the job on a click outside select mode', () => {
    renderCard({ job: job('completed') })

    fireEvent.click(screen.getByText('Ocean talk'))

    expect(navigate).toHaveBeenCalledWith({ to: '/jobs/$jobId', params: { jobId: 'job-1' } })
  })

  describe('select mode', () => {
    it('toggles the selection instead of opening the job, with no action buttons', () => {
      const onToggleSelect = vi.fn()
      renderCard({ job: job('completed'), selectable: true, selected: true, onToggleSelect })

      fireEvent.click(screen.getByText('Ocean talk'))

      expect(onToggleSelect).toHaveBeenCalledWith('job-1')
      expect(navigate).not.toHaveBeenCalled()
      expect(screen.getByRole('checkbox', { name: /select ocean talk/i })).toHaveAttribute('aria-checked', 'true')
      expect(screen.queryByRole('button')).toBeNull()
    })

    it('never selects a running job (it must be cancelled before it can be deleted)', () => {
      const onToggleSelect = vi.fn()
      renderCard({ job: job('running'), selectable: true, onToggleSelect })

      fireEvent.click(screen.getByText('Ocean talk'))

      expect(onToggleSelect).not.toHaveBeenCalled()
      expect(screen.getByRole('checkbox')).toHaveAttribute('aria-disabled', 'true')
    })
  })
})
