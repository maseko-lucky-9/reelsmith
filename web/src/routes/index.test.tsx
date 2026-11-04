import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'
import type { JobState } from '@/api/client'

const navigateMock = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigateMock,
}))

vi.mock('@/api/client', () => ({
  api: {
    listJobs: vi.fn(),
    cancelJob: vi.fn(),
    deleteJob: vi.fn(),
  },
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

import { api } from '@/api/client'
import { toast } from 'sonner'
import { indexRoute } from './index'

function job(id: string, status: JobState['status']): JobState {
  return { job_id: id, status, title: `Talk ${id}`, url: `https://y.test/${id}`, output_paths: [] } as unknown as JobState
}

const JOBS = [job('a', 'completed'), job('b', 'failed'), job('c', 'running'), job('d', 'cancelled')]

async function renderHome() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(qc, 'invalidateQueries')
  const Page = indexRoute.options.component as ComponentType
  render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
  await screen.findByText('Talk a')
  return { invalidate, qc }
}

function card(name: string): HTMLElement {
  return screen.getByText(name).closest('[data-testid="project-card"]') as HTMLElement
}

function deleteSelectedButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: /delete selected/i }) as HTMLButtonElement
}

let confirmAnswer = true

beforeEach(() => {
  confirmAnswer = true
  vi.stubGlobal('confirm', vi.fn(() => confirmAnswer))
  vi.mocked(api.listJobs).mockReset().mockResolvedValue(JOBS)
  vi.mocked(api.deleteJob).mockReset().mockResolvedValue(undefined)
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('home page: Search YouTube (spec 015)', () => {
  it('has a Search YouTube button beside the new-project entry that opens the search page', async () => {
    navigateMock.mockReset()
    await renderHome()

    fireEvent.click(screen.getByRole('button', { name: 'Search YouTube' }))

    expect(navigateMock).toHaveBeenCalledWith({ to: '/sources/youtube' })
  })
})

describe('home page: select and delete', () => {
  it('has no selection bar until Select is pressed', async () => {
    await renderHome()

    expect(screen.queryByRole('toolbar', { name: /selected projects/i })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))

    expect(screen.getByRole('toolbar', { name: /selected projects/i })).toBeInTheDocument()
    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (0)')
    expect(deleteSelectedButton()).toBeDisabled()
  })

  it('deletes each selected job with its own DELETE, then refreshes the list', async () => {
    const { invalidate } = await renderHome()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    fireEvent.click(card('Talk a'))
    fireEvent.click(card('Talk d'))

    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (2)')
    fireEvent.click(deleteSelectedButton())

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Deleted 2 jobs'))
    expect(vi.mocked(api.deleteJob).mock.calls.map((c) => c[0]).sort()).toEqual(['a', 'd'])
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['jobs'] })
    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (0)')
  })

  it('select all picks every job that can be deleted, never a running one', async () => {
    await renderHome()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))

    fireEvent.click(within(screen.getByRole('toolbar')).getByRole('checkbox', { name: /select all/i }))
    fireEvent.click(deleteSelectedButton())

    await waitFor(() => expect(api.deleteJob).toHaveBeenCalledTimes(3))
    expect(vi.mocked(api.deleteJob).mock.calls.map((c) => c[0]).sort()).toEqual(['a', 'b', 'd'])
  })

  it('a running job cannot be selected', async () => {
    await renderHome()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))

    fireEvent.click(card('Talk c'))

    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (0)')
  })

  it('reports the jobs that could not be deleted and still deletes the rest', async () => {
    vi.mocked(api.deleteJob).mockImplementation((id: string) =>
      id === 'b' ? Promise.reject(new Error('409')) : Promise.resolve(undefined),
    )
    await renderHome()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    fireEvent.click(card('Talk a'))
    fireEvent.click(card('Talk b'))

    fireEvent.click(deleteSelectedButton())

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Deleted 1 of 2; could not delete: Talk b'))
    expect(api.deleteJob).toHaveBeenCalledTimes(2)
    // The failed one stays selected for a retry.
    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (1)')
    expect(card('Talk b').querySelector('[role="checkbox"]')).toHaveAttribute('aria-checked', 'true')
  })

  it("forgets the deleted jobs' cached pages, keeps the failed one's", async () => {
    vi.mocked(api.deleteJob).mockImplementation((id: string) =>
      id === 'b' ? Promise.reject(new Error('409')) : Promise.resolve(undefined),
    )
    const { qc } = await renderHome()
    qc.setQueryData(['job', 'a'], { cached: true })
    qc.setQueryData(['clips', 'a'], [])
    qc.setQueryData(['job', 'b'], { cached: true })
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    fireEvent.click(card('Talk a'))
    fireEvent.click(card('Talk b'))

    fireEvent.click(deleteSelectedButton())

    await waitFor(() => expect(qc.getQueryData(['job', 'a'])).toBeUndefined())
    expect(qc.getQueryData(['clips', 'a'])).toBeUndefined()
    expect(qc.getQueryData(['job', 'b'])).toEqual({ cached: true })
  })

  it('counts a job another request is already deleting as deleted, not failed', async () => {
    vi.mocked(api.deleteJob).mockImplementation((id: string) =>
      id === 'b'
        ? Promise.reject(new Error('409 {"detail":"this job is already being deleted"}'))
        : Promise.resolve(undefined),
    )
    await renderHome()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    fireEvent.click(card('Talk a'))
    fireEvent.click(card('Talk b'))

    fireEvent.click(deleteSelectedButton())

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Deleted 2 jobs'))
    expect(toast.error).not.toHaveBeenCalled()
    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (0)')
  })

  it('select all toggles: a second click clears the selection', async () => {
    await renderHome()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    const selectAll = within(screen.getByRole('toolbar')).getByRole('checkbox', { name: /select all/i })

    fireEvent.click(selectAll)
    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (3)')
    fireEvent.click(selectAll)

    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (0)')
  })

  it('select all is disabled when every job is still running', async () => {
    vi.mocked(api.listJobs).mockResolvedValue([job('x', 'running'), job('y', 'pending')])
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const Page = indexRoute.options.component as ComponentType
    render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
    await screen.findByText('Talk x')
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))

    expect(within(screen.getByRole('toolbar')).getByRole('checkbox', { name: /select all/i })).toBeDisabled()
  })

  it('on the Saved tab, select all picks only the completed jobs shown', async () => {
    await renderHome()
    fireEvent.click(screen.getByRole('button', { name: /saved/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))

    fireEvent.click(within(screen.getByRole('toolbar')).getByRole('checkbox', { name: /select all/i }))
    fireEvent.click(deleteSelectedButton())

    await waitFor(() => expect(api.deleteJob).toHaveBeenCalledTimes(1))
    expect(api.deleteJob).toHaveBeenCalledWith('a')
  })

  it('refreshes the list while a job is running, and stops when none is', async () => {
    await renderHome()
    const calls = vi.mocked(api.listJobs).mock.calls.length

    await waitFor(() => expect(vi.mocked(api.listJobs).mock.calls.length).toBeGreaterThan(calls), { timeout: 4500 })
  }, 10_000)

  it('does not poll when no job is running', async () => {
    vi.mocked(api.listJobs).mockResolvedValue([job('a', 'completed')])
    await renderHome()
    const calls = vi.mocked(api.listJobs).mock.calls.length

    await new Promise((resolve) => setTimeout(resolve, 3500))

    expect(vi.mocked(api.listJobs).mock.calls.length).toBe(calls)
  }, 10_000)

  it('deletes nothing when the confirm is declined', async () => {
    confirmAnswer = false
    await renderHome()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    fireEvent.click(card('Talk a'))

    fireEvent.click(deleteSelectedButton())

    expect(api.deleteJob).not.toHaveBeenCalled()
  })

  it('leaving select mode clears the selection', async () => {
    await renderHome()
    fireEvent.click(screen.getByRole('button', { name: 'Select' }))
    fireEvent.click(card('Talk a'))
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))

    fireEvent.click(screen.getByRole('button', { name: 'Select' }))

    expect(deleteSelectedButton()).toHaveTextContent('Delete selected (0)')
  })
})
