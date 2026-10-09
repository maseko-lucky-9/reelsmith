import type { PublishJob } from '@/api/client'

/** Statuses after which a publish job never changes (app/services/social_publish_service.py). */
export const PUBLISH_TERMINAL_STATUSES: ReadonlySet<PublishJob['status']> = new Set([
  'published',
  'posted_unverified',
  'failed',
  'cancelled',
])

export const PUBLISH_POLL_MS = 3000

/**
 * True while the backend is actively working a publish job: queued or posting, or
 * pending and due now. A pending job scheduled for later is waiting, not in flight.
 */
export function isPublishInFlight(job: PublishJob, now: number = Date.now()): boolean {
  if (PUBLISH_TERMINAL_STATUSES.has(job.status)) return false
  if (job.status !== 'pending' || !job.schedule_at) return true
  const due = Date.parse(job.schedule_at)
  return Number.isNaN(due) || due <= now
}

/** refetchInterval for a clip's publish history: poll only while a publish is in flight. */
export function publishHistoryRefetchInterval(
  jobs: PublishJob[] | undefined,
  now: number = Date.now(),
): number | false {
  return jobs?.some((j) => isPublishInFlight(j, now)) ? PUBLISH_POLL_MS : false
}
