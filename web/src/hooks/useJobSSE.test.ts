import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { useJobSSE } from './useJobSSE'
import { KNOWN_EVENT_TYPES, type PipelineEvent } from '@/lib/pipelineStages'

type Listener = (e: MessageEvent) => void

/**
 * jsdom has no EventSource. This fake mirrors the browser contract that matters:
 * the backend sends NAMED events (`event: <EventType>`), and a real EventSource
 * delivers those ONLY to addEventListener(<EventType>) listeners — `onmessage`
 * fires solely for unnamed events (event type "message").
 */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  closed = false
  onmessage: Listener | null = null
  onerror: (() => void) | null = null
  private listeners: Record<string, Listener[]> = {}

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: Listener) {
    this.listeners[type] = [...(this.listeners[type] ?? []), fn]
  }
  removeEventListener(type: string, fn: Listener) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((l) => l !== fn)
  }
  listenerTypes(): string[] {
    return Object.keys(this.listeners).filter((t) => this.listeners[t].length > 0)
  }
  /** Named event, exactly as sse-starlette sends it: `event:` + `id:` + `data:`. */
  emitNamed(type: string, data: unknown, id = '') {
    if (this.closed) return
    const e = { type, data: JSON.stringify(data), lastEventId: id } as MessageEvent
    this.listeners[type]?.forEach((fn) => fn(e))
  }
  /** Unnamed event (no `event:` line) — the only kind a browser routes to onmessage. */
  emitUnnamed(data: unknown) {
    if (this.closed) return
    const e = { type: 'message', data: JSON.stringify(data), lastEventId: '' } as MessageEvent
    this.onmessage?.(e)
    this.listeners.message?.forEach((fn) => fn(e))
  }
  fail() {
    this.onerror?.()
  }
  close() {
    this.closed = true
  }
}

const JOB = 'job-1'

/** Shape of app/domain/events.py Event.to_dict(). */
function backendEvent(type: string, eventId: string, payload: Record<string, unknown> = {}) {
  return {
    event_id: eventId,
    type,
    job_id: JOB,
    correlation_id: JOB,
    occurred_at: '2026-10-09T00:00:00+00:00',
    payload,
  }
}

function emit(es: FakeEventSource, type: string, eventId: string, payload: Record<string, unknown> = {}) {
  es.emitNamed(type, backendEvent(type, eventId, payload), eventId)
}

function setup(jobId: string | undefined, opts?: { enabled?: boolean }) {
  const qc = new QueryClient()
  const invalidate = vi.spyOn(qc, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children)
  const hook = renderHook(
    (props: { enabled?: boolean }) => useJobSSE(jobId, props),
    { wrapper, initialProps: opts ?? {} },
  )
  return { qc, invalidate, hook }
}

function invalidatedKeys(spy: { mock: { calls: unknown[][] } }): unknown[][] {
  return spy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey)
}

function latestEs(): FakeEventSource {
  return FakeEventSource.instances[FakeEventSource.instances.length - 1]
}

/** Let the hook's coalesced invalidation timer fire. */
function flush() {
  act(() => {
    vi.advanceTimersByTime(1000)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('useJobSSE — connection', () => {
  it('opens an EventSource for the given jobId', () => {
    setup('job-test')
    expect(FakeEventSource.instances.length).toBe(1)
    expect(FakeEventSource.instances[0].url).toBe('/api/jobs/job-test/events')
  })

  it('does not open EventSource when jobId is undefined', () => {
    setup(undefined)
    expect(FakeEventSource.instances.length).toBe(0)
  })

  it('does not open EventSource when disabled (job already terminal on load)', () => {
    setup(JOB, { enabled: false })
    expect(FakeEventSource.instances.length).toBe(0)
  })

  it('registers a named-event listener for EVERY known backend event type', () => {
    setup(JOB)
    const types = new Set(latestEs().listenerTypes())
    for (const t of KNOWN_EVENT_TYPES) expect(types.has(t), `missing listener for ${t}`).toBe(true)
  })
})

describe('useJobSSE — named events (real browser delivery)', () => {
  it('a named ChapterTranscribed appends to job-events and invalidates only this job + its clips', () => {
    const { qc, invalidate } = setup(JOB)
    act(() => emit(latestEs(), 'ChapterTranscribed', 'ev-1', { chapter_index: 0 }))

    expect(qc.getQueryData<PipelineEvent[]>(['job-events', JOB])).toEqual([
      { id: 'ev-1', type: 'ChapterTranscribed', payload: { chapter_index: 0 } },
    ])

    flush()
    const keys = invalidatedKeys(invalidate)
    expect(keys).toContainEqual(['job', JOB])
    expect(keys).toContainEqual(['clips', JOB])
  })

  it('non-terminal events never invalidate the global jobs list or the global clips list', () => {
    const { invalidate } = setup(JOB)
    const es = latestEs()
    act(() => {
      emit(es, 'VideoRequested', 'ev-1')
      emit(es, 'FolderCreated', 'ev-2')
      emit(es, 'ChaptersDetected', 'ev-3')
      emit(es, 'ClipRendered', 'ev-4', { chapter_index: 0 })
    })
    flush()
    const keys = invalidatedKeys(invalidate)
    expect(keys.length).toBeGreaterThan(0)
    for (const k of keys) {
      expect([JSON.stringify(['job', JOB]), JSON.stringify(['clips', JOB])]).toContain(JSON.stringify(k))
    }
  })

  it('coalesces a burst of events (history replay) into one invalidation per key', () => {
    const { invalidate } = setup(JOB)
    const es = latestEs()
    act(() => {
      for (let i = 0; i < 20; i++) emit(es, 'ChapterTranscribed', `ev-${i}`, { chapter_index: i })
    })
    flush()
    const keys = invalidatedKeys(invalidate).map((k) => JSON.stringify(k))
    expect(keys.filter((k) => k === JSON.stringify(['job', JOB])).length).toBe(1)
    expect(keys.filter((k) => k === JSON.stringify(['clips', JOB])).length).toBe(1)
  })

  it.each(['JobCompleted', 'JobFailed'])('%s invalidates job, clips AND the jobs list, then closes', (type) => {
    const { qc, invalidate } = setup(JOB)
    const es = latestEs()
    act(() => emit(es, type, 'ev-term'))
    const keys = invalidatedKeys(invalidate)
    expect(keys).toContainEqual(['jobs'])
    expect(keys).toContainEqual(['job', JOB])
    expect(keys).toContainEqual(['clips', JOB])
    expect(es.closed).toBe(true)
    expect(qc.getQueryData<PipelineEvent[]>(['job-events', JOB])?.map((e) => e.type)).toEqual([type])
  })

  it('reconnect history replay does not duplicate events already in the cache', () => {
    const { qc } = setup(JOB)
    const first = latestEs()
    act(() => {
      emit(first, 'VideoRequested', 'ev-1')
      emit(first, 'FolderCreated', 'ev-2')
      first.fail()
    })
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    const second = latestEs()
    expect(second).not.toBe(first)
    // Backend AsyncEventBus.subscribe replays the job's history on every new connection.
    act(() => {
      emit(second, 'VideoRequested', 'ev-1')
      emit(second, 'FolderCreated', 'ev-2')
      emit(second, 'VideoDownloaded', 'ev-3')
    })
    expect(qc.getQueryData<PipelineEvent[]>(['job-events', JOB])?.map((e) => e.id)).toEqual([
      'ev-1',
      'ev-2',
      'ev-3',
    ])
  })

  it('still accepts unnamed events through onmessage (fallback)', () => {
    const { qc } = setup(JOB)
    act(() => latestEs().emitUnnamed(backendEvent('FolderCreated', 'ev-u')))
    expect(qc.getQueryData<PipelineEvent[]>(['job-events', JOB])?.map((e) => e.type)).toEqual([
      'FolderCreated',
    ])
  })
})

describe('useJobSSE — SSE failure fallback', () => {
  function failThreeTimes() {
    for (let i = 0; i < 3; i++) {
      act(() => latestEs().fail())
      act(() => {
        vi.advanceTimersByTime(10_000)
      })
    }
  }

  it('after 3 failed connects, polls BOTH the job and its clips every 3s', () => {
    const { invalidate } = setup(JOB)
    failThreeTimes()
    invalidate.mockClear()
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    const keys = invalidatedKeys(invalidate)
    expect(keys).toContainEqual(['job', JOB])
    expect(keys).toContainEqual(['clips', JOB])
    expect(keys).not.toContainEqual(['jobs'])
  })

  it('stops the fallback poll when the job becomes terminal (enabled → false)', () => {
    const { invalidate, hook } = setup(JOB, { enabled: true })
    failThreeTimes()
    hook.rerender({ enabled: false })
    invalidate.mockClear()
    act(() => {
      vi.advanceTimersByTime(30_000)
    })
    expect(invalidate).not.toHaveBeenCalled()
  })

  it('closes the open EventSource when the job becomes terminal', () => {
    const { hook } = setup(JOB, { enabled: true })
    const es = latestEs()
    hook.rerender({ enabled: false })
    expect(es.closed).toBe(true)
  })
})
