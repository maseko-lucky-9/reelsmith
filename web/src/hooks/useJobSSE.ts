import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { KNOWN_EVENT_TYPES, TERMINAL_EVENT_TYPES, type PipelineEvent } from '@/lib/pipelineStages'

export interface UseJobSSEOptions {
  /**
   * Set false when there is nothing to stream — e.g. the job is not loaded yet or is
   * already completed/failed. Disabling closes the stream and stops the fallback poll.
   */
  enabled?: boolean
}

/** Window in which a burst of events (e.g. history replay on connect) becomes one refetch. */
const INVALIDATE_COALESCE_MS = 500
const FALLBACK_POLL_MS = 3000
const MAX_FAILED_CONNECTS = 3

interface WireEvent {
  event_id?: string
  type?: string
  payload?: Record<string, unknown>
}

function parseWireEvent(data: unknown): WireEvent {
  if (typeof data !== 'string') return {}
  try {
    const parsed: unknown = JSON.parse(data)
    return parsed && typeof parsed === 'object' ? (parsed as WireEvent) : {}
  } catch {
    return {}
  }
}

/**
 * Subscribes to /api/jobs/{jobId}/events and mirrors every event into the React Query
 * cache under ['job-events', jobId] (read by JobProgressTimeline), then refreshes the
 * job's own queries: ['job', jobId] and ['clips', jobId]. The global ['jobs'] list is
 * refreshed only on terminal events.
 *
 * The backend sends NAMED events (`event: <EventType>`). Browsers deliver those only to
 * listeners registered for that exact name — never to `onmessage` — so one listener is
 * registered per backend EventType. `onmessage` stays as a fallback for unnamed events.
 *
 * Every new connection replays the job's event history (AsyncEventBus.subscribe), so
 * events are de-duplicated by backend event_id.
 *
 * Reconnect: exponential backoff on error; after 3 failed attempts it falls back to
 * polling the job and its clips every 3s until the job turns terminal (enabled=false).
 */
export function useJobSSE(jobId: string | undefined, { enabled = true }: UseJobSSEOptions = {}) {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!jobId || !enabled) return

    const jobKey = ['job', jobId] as const
    const clipsKey = ['clips', jobId] as const
    const eventsKey = ['job-events', jobId] as const
    const seenUnknown = new Set<string>()

    let es: EventSource | null = null
    let closed = false
    let failedConnects = 0
    let pollInterval: ReturnType<typeof setInterval> | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let invalidateTimer: ReturnType<typeof setTimeout> | null = null

    function invalidateJobQueries() {
      void queryClient.invalidateQueries({ queryKey: jobKey })
      void queryClient.invalidateQueries({ queryKey: clipsKey })
    }

    function scheduleInvalidate() {
      if (invalidateTimer) return
      invalidateTimer = setTimeout(() => {
        invalidateTimer = null
        invalidateJobQueries()
      }, INVALIDATE_COALESCE_MS)
    }

    function stopTimers() {
      if (pollInterval) clearInterval(pollInterval)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (invalidateTimer) clearTimeout(invalidateTimer)
      pollInterval = reconnectTimer = invalidateTimer = null
    }

    function shutdown() {
      closed = true
      es?.close()
      stopTimers()
    }

    function startPoll() {
      if (pollInterval) return
      pollInterval = setInterval(invalidateJobQueries, FALLBACK_POLL_MS)
    }

    /** Appends to the events cache; returns false when the event id was already seen. */
    function pushEvent(event: PipelineEvent): boolean {
      const prev = queryClient.getQueryData<PipelineEvent[]>(eventsKey) ?? []
      if (event.id && prev.some((e) => e.id === event.id)) return false
      if (!KNOWN_EVENT_TYPES.has(event.type) && !seenUnknown.has(event.type)) {
        seenUnknown.add(event.type)
        console.warn('[useJobSSE] unknown event type (frontend/backend drift?):', event.type)
      }
      queryClient.setQueryData<PipelineEvent[]>(eventsKey, [...prev, event])
      return true
    }

    function handleEvent(raw: MessageEvent, namedType?: string) {
      failedConnects = 0
      const parsed = parseWireEvent(raw.data)
      const type = parsed.type ?? namedType
      if (!type) return
      const id = parsed.event_id ?? (raw.lastEventId || undefined)
      const isNew = pushEvent({ id, type, payload: parsed.payload })

      if (TERMINAL_EVENT_TYPES.has(type)) {
        // The backend ends the stream after a terminal event; close before the browser retries.
        shutdown()
        invalidateJobQueries()
        void queryClient.invalidateQueries({ queryKey: ['jobs'] })
        return
      }
      if (isNew) scheduleInvalidate()
    }

    const onNamedEvent = (e: Event) => {
      const named = e as MessageEvent
      handleEvent(named, named.type)
    }

    function connect() {
      reconnectTimer = null
      if (closed) return
      es?.close()

      const source = new EventSource(`/api/jobs/${jobId}/events`)
      es = source
      for (const type of KNOWN_EVENT_TYPES) source.addEventListener(type, onNamedEvent)
      source.onmessage = (e) => handleEvent(e)
      source.onerror = () => {
        source.close()
        if (closed) return
        failedConnects += 1
        if (failedConnects >= MAX_FAILED_CONNECTS) {
          startPoll()
          return
        }
        const delay = Math.min(1000 * 2 ** failedConnects, 30000)
        reconnectTimer = setTimeout(connect, delay)
      }
    }

    connect()
    return shutdown
  }, [jobId, enabled, queryClient])
}
