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
 * Statuses the backend is actively working. Publishing is immediate-only (FR-032 dropped):
 * `pending` only survives on rows created by the old scheduler, which nothing advances.
 */
const IN_FLIGHT_STATUSES: ReadonlySet<PublishJob['status']> = new Set(['queued', 'posting'])

export function isPublishInFlight(job: PublishJob): boolean {
  return IN_FLIGHT_STATUSES.has(job.status)
}

/** refetchInterval for a clip's publish history: poll only while a publish is in flight. */
export function publishHistoryRefetchInterval(jobs: PublishJob[] | undefined): number | false {
  return jobs?.some(isPublishInFlight) ? PUBLISH_POLL_MS : false
}
