import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export function useJobSSE(jobId: string | undefined) {
  const queryClient = useQueryClient()
  const attemptsRef = useRef(0)
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!jobId) return

    let pollInterval: ReturnType<typeof setInterval> | null = null
    let closed = false

    function startPoll() {
      if (pollInterval) return
      pollInterval = setInterval(() => {
        void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
      }, 3000)
    }

    function connect() {
      if (closed) return
      esRef.current?.close()

      const es = new EventSource(`/api/jobs/${jobId}/events`)
      esRef.current = es

      es.onmessage = () => {
        attemptsRef.current = 0
        void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
        void queryClient.invalidateQueries({ queryKey: ['jobs'] })
        void queryClient.invalidateQueries({ queryKey: ['clips'] })
      }

      es.addEventListener('JobCompleted', () => {
        void queryClient.invalidateQueries({ queryKey: ['job', jobId] })
        void queryClient.invalidateQueries({ queryKey: ['jobs'] })
        void queryClient.invalidateQueries({ queryKey: ['clips'] })
        es.close()
        if (pollInterval) clearInterval(pollInterval)
      })

      es.addEventListener('JobFailed', () => {
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
