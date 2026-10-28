import type { QueryClient } from '@tanstack/react-query'
import type { JobState, JobStatus } from '@/api/client'

/**
 * A job in one of these states can be cancelled (POST /api/jobs/{id}/cancel), and cannot be
 * deleted until it is: DELETE /api/jobs/{id} answers 409 (app/routers/jobs.py _ACTIVE_STATUSES).
 */
export const ACTIVE_JOB_STATUSES: ReadonlySet<JobStatus> = new Set(['pending', 'running'])

export function isActiveJob(job: JobState): boolean {
  return ACTIVE_JOB_STATUSES.has(job.status)
}

/** apiFetch errors read `"<status> <body>"`: a 409 means the server refused for the job's state. */
export function isConflict(error: unknown): boolean {
  return error instanceof Error && error.message.startsWith('409')
}

// 409 details of DELETE /api/jobs/{id} (app/routers/jobs.py DELETE_*).
/** Only worker threads are still finishing: nothing to cancel, try again shortly. */
export function isStillFinishing(error: unknown): boolean {
  return isConflict(error) && (error as Error).message.includes('still finishing')
}

/** Another request is deleting the job right now: as good as deleted. */
export function isAlreadyBeingDeleted(error: unknown): boolean {
  return isConflict(error) && (error as Error).message.includes('already being deleted')
}

/** Something a cancel can stop still runs (the job, a re-render, a reprompt). */
export function needsCancelFirst(error: unknown): boolean {
  return isConflict(error) && (error as Error).message.includes('cancel it first')
}

/**
 * Drops the cached detail of a deleted job (its page, clips and events), not just the list,
 * and refreshes the all-clips list, which held its clips.
 */
export function forgetJob(queryClient: QueryClient, jobId: string): void {
  for (const key of ['job', 'clips', 'job-events']) {
    queryClient.removeQueries({ queryKey: [key, jobId] })
  }
  void queryClient.invalidateQueries({ queryKey: ['clips'], exact: true })
}

/** After a `cancelling` answer the job's status changes only once its tasks unwind. */
export const CANCEL_REFRESH_MS = 1500
