import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { KNOWN_EVENT_TYPES, type PipelineEvent } from '@/lib/pipelineStages'

/**
 * Subscribes to /api/jobs/{jobId}/events. Mirrors every received event into
 * the React Query cache under ['job-events', jobId] so that JobProgressTimeline
 * can read live progress through useQuery (no prop drilling).
 *
 * Reconnect: exponential backoff up to 30s on EventSource error; after 3
 * failed attempts, falls back to 3s polling on the JobState query.
 */
export function useJobSSE(jobId: string | undefined) {
  const queryClient = useQueryClient()
  const attemptsRef = useRef(0)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!jobId) return

    let pollInterval: ReturnType<typeof setInterval> | null = null
    let closed = false
    const eventsKey = ['job-events', jobId] as const
    const seenUnknown = new Set<string>()

    function startPoll() {
      if (pollInterval) return
      pollInterval = setInterval(() => {
        void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
      }, 3000)
    }

    function pushEvent(type: string, payload: Record<string, unknown> | undefined) {
      if (!KNOWN_EVENT_TYPES.has(type) && !seenUnknown.has(type)) {
        seenUnknown.add(type)
        // eslint-disable-next-line no-console
        console.warn('[useJobSSE] unknown event type (frontend STAGES drift?):', type)
      }
      queryClient.setQueryData<PipelineEvent[]>(eventsKey, (prev) => [
        ...(prev ?? []),
        { type, payload },
      ])
    }

    function connect() {
      if (closed) return
      esRef.current?.close()

      const es = new EventSource(`/api/jobs/${jobId}/events`)
      esRef.current = es

      es.onmessage = (raw) => {
        attemptsRef.current = 0
        // Generic message: parse as { type, payload } if data is JSON.
        let parsed: { type?: string; payload?: Record<string, unknown> } = {}
        try {
          parsed = JSON.parse(raw.data ?? '{}')
        } catch {
          parsed = {}
        }
        if (parsed.type) pushEvent(parsed.type, parsed.payload)
        void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
        void queryClient.invalidateQueries({ queryKey: ['jobs'] })
        void queryClient.invalidateQueries({ queryKey: ['clips'] })
      }

      es.addEventListener('JobCompleted', (e: MessageEvent) => {
        let parsed: { type?: string; payload?: Record<string, unknown> } = {}
        try {
          parsed = JSON.parse(e.data ?? '{}')
        } catch {
          /* ignore */
        }
        pushEvent('JobCompleted', parsed.payload)
        void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
        void queryClient.invalidateQueries({ queryKey: ['jobs'] })
        void queryClient.invalidateQueries({ queryKey: ['clips'] })
        es.close()
        if (pollInterval) clearInterval(pollInterval)
      })

      es.addEventListener('JobFailed', (e: MessageEvent) => {
        let parsed: { type?: string; payload?: Record<string, unknown> } = {}
        try {
          parsed = JSON.parse(e.data ?? '{}')
        } catch {
          /* ignore */
        }
        pushEvent('JobFailed', parsed.payload)
        void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
        es.close()
        if (pollInterval) clearInterval(pollInterval)
      })

      es.onerror = () => {
        es.close()
        attemptsRef.current += 1
        if (attemptsRef.current >= 3) {
          startPoll()
          return
        }
        const delay = Math.min(1000 * 2 ** attemptsRef.current, 30000)
        setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      closed = true
      esRef.current?.close()
      if (pollInterval) clearInterval(pollInterval)
    }
  }, [jobId, queryClient])
}
