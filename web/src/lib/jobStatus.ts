import type { JobState, JobStatus } from '@/api/client'

/**
 * A job in one of these states can be cancelled (POST /api/jobs/{id}/cancel), and cannot be
 * deleted until it is: DELETE /api/jobs/{id} answers 409 (app/routers/jobs.py _ACTIVE_STATUSES).
 */
export const ACTIVE_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(['pending', 'running'])

export function isActiveJob(job: JobState): boolean {
  return ACTIVE_JOB_STATUSES.has(job.status)
}
