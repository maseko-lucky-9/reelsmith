/** Typed API helpers wrapping fetch against the FastAPI backend. */

export interface JobState {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  current_step: string | null
  url: string
  download_path: string
  caption_format: string
  target_aspect_ratio: number
  title: string | null
  duration: number | null
  error: string | null
  output_paths: string[]
}

export interface ClipRecord {
  clip_id: string
  job_id: string
  chapter_id: string | null
  start: number
  end: number
  output_path: string | null
  thumbnail_path: string | null
  title: string | null
  summary: string | null
  virality_score: number | null
  score_breakdown: Record<string, number> | null
  transcript: Record<string, unknown> | null
  retired: boolean
}

export interface CreateJobRequest {
  url: string
  download_path: string
  caption_format?: string
  target_aspect_ratio?: number
  language?: string
  segment_mode?: 'auto' | 'chapter'
}

export interface CreateJobResponse {
  job_id: string
  status: string
}

const BASE = ''

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${text}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => apiFetch<{ status: string; job_store: string }>('/api/health'),

  listJobs: (params?: { limit?: number; offset?: number; search?: string }) => {
    const q = new URLSearchParams()
    if (params?.limit != null) q.set('limit', String(params.limit))
    if (params?.offset != null) q.set('offset', String(params.offset))
    if (params?.search) q.set('search', params.search)
    return apiFetch<JobState[]>(`/jobs?${q}`)
  },

  getJob: (id: string) => apiFetch<JobState>(`/jobs/${id}`),

  createJob: (req: CreateJobRequest) =>
    apiFetch<CreateJobResponse>('/jobs', { method: 'POST', body: JSON.stringify(req) }),

  listClips: (params?: { job_id?: string; min_score?: number; search?: string }) => {
    const q = new URLSearchParams()
    if (params?.job_id) q.set('job_id', params.job_id)
    if (params?.min_score != null) q.set('min_score', String(params.min_score))
    if (params?.search) q.set('search', params.search)
    return apiFetch<ClipRecord[]>(`/clips?${q}`)
  },
}
