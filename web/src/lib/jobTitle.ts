import type { JobState } from '@/api/client'

const ACTIVE: ReadonlyArray<JobState['status']> = ['pending', 'running']

const STATUS_LABELS: Record<JobState['status'], string> = {
  pending: 'Queued',
  running: 'Processing…',
  completed: 'Completed',
  failed: 'Failed',
}

/**
 * Heading for a job: its title; a placeholder only while the job can still
 * produce one; and for a finished job with no title (rows from before titles
 * were stored) the source address. Never a server file path: an uploaded
 * file's URL is `upload:///…`, so only http(s) addresses are shown.
 */
export function jobDisplayTitle(job: Pick<JobState, 'title' | 'status' | 'url'>): string {
  const title = job.title?.trim()
  if (title) return title
  if (ACTIVE.includes(job.status)) return 'Loading title…'
  try {
    const url = new URL(job.url)
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      return url.hostname.replace(/^www\./, '') + url.pathname.replace(/\/$/, '') + url.search
    }
  } catch {
    // not a URL: fall through
  }
  return 'Untitled video'
}

export function jobStatusLabel(status: JobState['status']): string {
  return STATUS_LABELS[status]
}
