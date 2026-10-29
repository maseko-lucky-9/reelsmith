import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ComponentType } from 'react'
import type { SourceCleanCheck, YoutubeSearchItem } from '@/api/client'

const navigate = vi.fn()

vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
}))

vi.mock('@/api/client', () => ({
  api: {
    searchYoutube: vi.fn(),
    checkYoutubeClean: vi.fn(),
  },
}))

// A busy server is retried after a pause of seconds; here, of milliseconds.
vi.mock('@/lib/youtubeSources', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/youtubeSources')>()),
  busyRetryDelay: () => 5,
}))

import { api } from '@/api/client'
import { youtubeSourcesRoute } from './sources.youtube'
import { formatDuration, MAX_CONCURRENT_CHECKS } from '@/lib/youtubeSources'

function item(id: string, title: string, extra: Partial<YoutubeSearchItem> = {}): YoutubeSearchItem {
  return {
    id,
    title,
    channel: `Channel ${id}`,
    duration_seconds: 125,
    thumbnail_url: `https://i.ytimg.com/vi/${id}/mqdefault.jpg`,
    url: `https://www.youtube.com/watch?v=${id}`,
    ...extra,
  }
}

const check = (clean: boolean | null): SourceCleanCheck => ({
  clean,
  confidence: 0.9,
  reason: clean === false ? 'white words at the bottom' : 'no captions',
  frames_checked: 4,
})

const never = () => new Promise<SourceCleanCheck>(() => {})

async function renderSearch(items: YoutubeSearchItem[]) {
  vi.mocked(api.searchYoutube).mockResolvedValue({ items })
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const Page = youtubeSourcesRoute.options.component as ComponentType
  render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))
  fireEvent.change(screen.getByLabelText('Search YouTube'), { target: { value: 'ted talk' } })
  fireEvent.click(screen.getByRole('button', { name: 'Search' }))
  await screen.findByText(items[0].title)
}

function card(title: string): HTMLElement {
  return screen.getByText(title).closest('[data-testid="source-card"]') as HTMLElement
}

beforeEach(() => {
  navigate.mockReset()
  vi.mocked(api.searchYoutube).mockReset()
  vi.mocked(api.checkYoutubeClean).mockReset()
})

describe('YouTube source search page', () => {
  it('searches with the typed query and shows thumbnail, title, channel and duration', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(never)

    await renderSearch([item('AAAAAAAAAAA', 'Sleep talk', { duration_seconds: 3725 })])

    expect(api.searchYoutube).toHaveBeenCalledWith('ted talk', 12, expect.any(AbortSignal))
    const c = card('Sleep talk')
    expect(within(c).getByText('Channel AAAAAAAAAAA')).toBeInTheDocument()
    expect(within(c).getByText('1:02:05')).toBeInTheDocument()
    expect(c.querySelector('img')).toHaveAttribute('src', 'https://i.ytimg.com/vi/AAAAAAAAAAA/mqdefault.jpg')
  })

  it('hides videos found to have captions, keeps clean ones and pending ones visible', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation((id) =>
      id === 'CAPTIONED_01' ? Promise.resolve(check(false)) : id === 'CLEAN_000001' ? Promise.resolve(check(true)) : never(),
    )

    await renderSearch([
      item('CAPTIONED_01', 'Captioned video'),
      item('CLEAN_000001', 'Clean video'),
      item('PENDING_0001', 'Pending video'),
    ])

    await waitFor(() => expect(screen.queryByText('Captioned video')).toBeNull())
    expect(within(card('Clean video')).getByTestId('clean-badge')).toHaveTextContent('Clean')
    expect(within(card('Pending video')).getByTestId('clean-badge')).toHaveAttribute('data-state', 'pending')
    expect(screen.getByText('(1 hidden)')).toBeInTheDocument()
  })

  it('has the "Hide videos with captions" toggle on by default and shows the hidden ones when it is turned off', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(() => Promise.resolve(check(false)))

    await renderSearch([item('CAPTIONED_01', 'Captioned video')])

    const toggle = screen.getByLabelText(/hide videos with captions/i) as HTMLInputElement
    expect(toggle.checked).toBe(true)
    await waitFor(() => expect(screen.queryByText('Captioned video')).toBeNull())

    fireEvent.click(toggle)

    expect(within(card('Captioned video')).getByTestId('clean-badge')).toHaveTextContent('Has captions')
  })

  it('shows a video whose check is unavailable as unchecked, not hidden', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(() => Promise.resolve(check(null)))

    await renderSearch([item('UNKNOWN_0001', 'Unknown video')])

    await waitFor(() =>
      expect(within(card('Unknown video')).getByTestId('clean-badge')).toHaveAttribute('data-state', 'unknown'),
    )
    // The page learns of the answer in an effect: wait for it, then check nothing was hidden.
    await act(async () => {})
    expect(screen.getByText('Unknown video')).toBeInTheDocument()
    expect(screen.queryByText(/hidden\)/)).toBeNull()
  })

  it('shows a video whose check request failed as unchecked, not hidden', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(() => Promise.reject(new Error('500 boom')))

    await renderSearch([item('FAILED_00001', 'Failed check video')])

    await waitFor(() =>
      expect(within(card('Failed check video')).getByTestId('clean-badge')).toHaveAttribute('data-state', 'unknown'),
    )
    // The page learns of the answer in an effect: wait for it, then check nothing was hidden.
    await act(async () => {})
    expect(screen.getByText('Failed check video')).toBeInTheDocument()
    expect(screen.queryByText(/hidden\)/)).toBeNull()
    // An error that is not "busy" is an answer: it is asked once, not retried.
    expect(api.checkYoutubeClean).toHaveBeenCalledTimes(1)
  })

  it('"Use this video" opens the workflow with that watch URL', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(never)

    await renderSearch([item('AAAAAAAAAAA', 'First'), item('BBBBBBBBBBB', 'Second')])
    fireEvent.click(within(card('Second')).getByRole('button', { name: 'Use this video' }))

    expect(navigate).toHaveBeenCalledWith({
      to: '/workflow',
      search: { url: 'https://www.youtube.com/watch?v=BBBBBBBBBBB' },
    })
  })

  it('runs at most 3 checks at once and starts the next one as one finishes', async () => {
    const resolvers = new Map<string, (c: SourceCleanCheck) => void>()
    vi.mocked(api.checkYoutubeClean).mockImplementation(
      (id) => new Promise<SourceCleanCheck>((resolve) => resolvers.set(id, resolve)),
    )
    const ids = Array.from({ length: 8 }, (_, i) => `VIDEO_${String(i).padStart(5, '0')}`)

    await renderSearch(ids.map((id, i) => item(id, `Video ${i}`)))

    await waitFor(() => expect(api.checkYoutubeClean).toHaveBeenCalledTimes(MAX_CONCURRENT_CHECKS))
    expect(vi.mocked(api.checkYoutubeClean).mock.calls.map((c) => c[0])).toEqual(ids.slice(0, 3))
    expect(within(card('Video 5')).getByTestId('clean-badge')).toHaveTextContent('Waiting')

    await act(async () => resolvers.get(ids[1])!(check(true)))

    await waitFor(() => expect(api.checkYoutubeClean).toHaveBeenCalledTimes(4))
    expect(vi.mocked(api.checkYoutubeClean).mock.calls[3][0]).toBe(ids[3])
  })

  it('asks for each video at most once, however often the page re-renders', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(() => Promise.resolve(check(true)))

    await renderSearch([item('AAAAAAAAAAA', 'First'), item('BBBBBBBBBBB', 'Second')])
    await waitFor(() => expect(within(card('Second')).getByTestId('clean-badge')).toHaveTextContent('Clean'))
    fireEvent.click(screen.getByLabelText(/hide videos with captions/i))
    fireEvent.click(screen.getByLabelText(/hide videos with captions/i))

    expect(api.checkYoutubeClean).toHaveBeenCalledTimes(2)
  })

  it('says so when the search fails', async () => {
    vi.mocked(api.searchYoutube).mockRejectedValue(new Error('504 YouTube search timed out'))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const Page = youtubeSourcesRoute.options.component as ComponentType
    render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))

    fireEvent.change(screen.getByLabelText('Search YouTube'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('504 YouTube search timed out')
  })

  it('does not search for an empty query', () => {
    const qc = new QueryClient()
    const Page = youtubeSourcesRoute.options.component as ComponentType
    render(createElement(QueryClientProvider, { client: qc }, createElement(Page)))

    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled()
    expect(api.searchYoutube).not.toHaveBeenCalled()
  })
})

describe('YouTube source search page: queue, busy server and retry', () => {
  const ids = ['VIDEO_AAAAA', 'VIDEO_BBBBB', 'VIDEO_CCCCC', 'VIDEO_DDDDD', 'VIDEO_EEEEE']
  const named = () => ids.map((id, i) => item(id, `Video ${'ABCDE'[i]}`))

  /** Check calls that are still open, and the most there ever were at once. */
  function trackedChecks(answer: (id: string, call: number) => Promise<SourceCleanCheck> | 'hang') {
    const state = { open: 0, maxOpen: 0, calls: new Map<string, number>(), settle: new Map<string, () => void>() }
    vi.mocked(api.checkYoutubeClean).mockImplementation((id) => {
      const call = (state.calls.get(id) ?? 0) + 1
      state.calls.set(id, call)
      state.open += 1
      state.maxOpen = Math.max(state.maxOpen, state.open)
      const result = answer(id, call)
      const release = () => {
        state.open -= 1
      }
      if (result === 'hang') {
        return new Promise<SourceCleanCheck>((resolve) => {
          state.settle.set(id, () => {
            release()
            resolve(check(true))
          })
        })
      }
      return result.finally(release)
    })
    return state
  }

  it('a failed check frees its slot and the next video in line is checked', async () => {
    const state = trackedChecks((id) => (id === ids[0] ? Promise.reject(new Error('500 boom')) : 'hang'))

    await renderSearch(named())

    await waitFor(() => expect(state.calls.has(ids[3])).toBe(true))
    expect(state.calls.has(ids[4])).toBe(false)
    expect(state.maxOpen).toBeLessThanOrEqual(MAX_CONCURRENT_CHECKS)
  })

  it('a busy server (429) is asked again, keeping the slot, and the answer then shows', async () => {
    const state = trackedChecks((id, call) =>
      id === ids[0] && call < 3 ? Promise.reject(new Error('429 too many checks')) : id === ids[0] ? Promise.resolve(check(true)) : 'hang',
    )

    await renderSearch(named())

    await waitFor(() =>
      expect(within(card('Video A')).getByTestId('clean-badge')).toHaveAttribute('data-state', 'clean'),
    )
    expect(state.calls.get(ids[0])).toBe(3)
    // While A was being retried it still held its slot, so D was not started early.
    expect(state.maxOpen).toBeLessThanOrEqual(MAX_CONCURRENT_CHECKS)
  })

  it('gives up on a server that stays busy after a few tries and shows the video as unchecked', async () => {
    const state = trackedChecks((id) => (id === ids[0] ? Promise.reject(new Error('429 too many checks')) : 'hang'))

    await renderSearch(named())

    await waitFor(() =>
      expect(within(card('Video A')).getByTestId('clean-badge')).toHaveAttribute('data-state', 'unknown'),
    )
    expect(state.calls.get(ids[0])).toBe(4) // the first try and 3 retries
  })

  it('a retry click shows Checking and runs the check again', async () => {
    const state = trackedChecks((id, call) =>
      id === ids[0] ? (call === 1 ? Promise.resolve(check(null)) : 'hang') : 'hang',
    )
    await renderSearch(named().slice(0, 1))
    await waitFor(() =>
      expect(within(card('Video A')).getByTestId('clean-badge')).toHaveAttribute('data-state', 'unknown'),
    )

    fireEvent.click(within(card('Video A')).getByTestId('clean-badge'))

    await waitFor(() => expect(state.calls.get(ids[0])).toBe(2))
    const badge = within(card('Video A')).getByTestId('clean-badge')
    expect(badge).toHaveAttribute('data-state', 'pending')
    expect(badge).toHaveTextContent('Checking')
    await act(async () => state.settle.get(ids[0])!())
    await waitFor(() =>
      expect(within(card('Video A')).getByTestId('clean-badge')).toHaveAttribute('data-state', 'clean'),
    )
  })

  it('a retry waits its turn behind the videos still in line, never exceeding 3 checks at once', async () => {
    const state = trackedChecks((id, call) =>
      id === ids[0] ? (call === 1 ? Promise.resolve(check(null)) : 'hang') : 'hang',
    )
    await renderSearch(named())
    await waitFor(() => expect(state.calls.has(ids[3])).toBe(true)) // A answered, B C D are running

    fireEvent.click(within(card('Video A')).getByTestId('clean-badge'))

    await act(async () => {})
    expect(within(card('Video A')).getByTestId('clean-badge')).toHaveTextContent('Waiting')
    expect(state.calls.get(ids[0])).toBe(1)

    await act(async () => state.settle.get(ids[1])!())
    await waitFor(() => expect(state.calls.has(ids[4])).toBe(true)) // E, still in line, goes first
    expect(state.calls.get(ids[0])).toBe(1)

    await act(async () => state.settle.get(ids[2])!())
    await waitFor(() => expect(state.calls.get(ids[0])).toBe(2)) // then the retry
    expect(state.maxOpen).toBeLessThanOrEqual(MAX_CONCURRENT_CHECKS)
  })

  it('names the cause on the Unchecked badge instead of always blaming Ollama', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(() =>
      Promise.resolve({ ...check(null), cause: 'stream_blocked' }),
    )

    await renderSearch([item('UNKNOWN_0001', 'Blocked video')])

    await waitFor(() => {
      const title = within(card('Blocked video')).getByTestId('clean-badge').getAttribute('title') ?? ''
      expect(title).toMatch(/YouTube refused/)
      expect(title).not.toMatch(/Ollama/)
    })
  })

  it('points at Ollama only when the cause is the model', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(() =>
      Promise.resolve({ ...check(null), cause: 'ollama_unavailable' }),
    )

    await renderSearch([item('UNKNOWN_0002', 'Model down video')])

    await waitFor(() =>
      expect(within(card('Model down video')).getByTestId('clean-badge').getAttribute('title')).toMatch(/Ollama/),
    )
  })

  it('passes an AbortSignal so a request is cancelled when its card goes away', async () => {
    vi.mocked(api.checkYoutubeClean).mockImplementation(never)

    await renderSearch([item('AAAAAAAAAAA', 'First')])

    await waitFor(() => expect(api.checkYoutubeClean).toHaveBeenCalled())
    expect(vi.mocked(api.checkYoutubeClean).mock.calls[0][1]).toBeInstanceOf(AbortSignal)
  })
})

describe('formatDuration', () => {
  it('formats minutes and hours, and live streams', () => {
    expect(formatDuration(59)).toBe('0:59')
    expect(formatDuration(125)).toBe('2:05')
    expect(formatDuration(3725)).toBe('1:02:05')
    expect(formatDuration(null)).toBe('Live')
  })
})
