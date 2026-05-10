/** Typed API helpers wrapping fetch against the FastAPI backend. */

export type ChapterStatus =
  | 'pending'
  | 'extracting'
  | 'transcribing'
  | 'captioning'
  | 'rendering'
  | 'completed'
  | 'failed'

export interface ChapterArtifacts {
  chapter_index: number
  status: ChapterStatus
  clip_path: string | null
  audio_path: string | null
  transcript: string | null
  captions_path: string | null
  image_paths: string[]
  output_path: string | null
  error: string | null
}

export interface JobState {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  current_step: string | null
  url: string
  source: string | null
  download_path: string
  caption_format: string
  target_aspect_ratio: number
  destination_folder: string | null
  clips_folder: string | null
  video_path: string | null
  title: string | null
  duration: number | null
  chapters: Record<string, ChapterArtifacts>
  output_paths: string[]
  error: string | null
  prompt?: string | null
  pipeline_options: PipelineOptions
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
  liked: boolean
  disliked: boolean
}

export interface BrandTemplate {
  id: string
  name: string
  logo_path: string | null
  font_path: string | null
  primary_color: string
  secondary_color: string
  caption_style: Record<string, unknown> | null
  intro_clip_path: string | null
  outro_clip_path: string | null
}

export interface PipelineOptions {
  transcription: boolean
  captions: boolean
  render: boolean
  segment_proposer: boolean
  reframe: boolean
  broll: boolean
  thumbnail: boolean
}

export interface VideoPreviewResponse {
  title: string
  duration: number
  resolution: string
  thumbnail: string
}

export interface CreateJobRequest {
  url: string
  download_path: string
  caption_format?: string
  target_aspect_ratio?: number
  language?: string
  segment_mode?: 'auto' | 'chapter'
  prompt?: string
  auto_hook?: boolean
  brand_template_id?: string
  pipeline_options?: PipelineOptions
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
    return apiFetch<JobState[]>(`/api/jobs?${q}`)
  },

  getJob: (id: string) => apiFetch<JobState>(`/api/jobs/${id}`),

  createJob: (req: CreateJobRequest) =>
    apiFetch<CreateJobResponse>('/api/jobs', { method: 'POST', body: JSON.stringify(req) }),

  previewVideo: (url: string) => {
    const q = new URLSearchParams({ url })
    return apiFetch<VideoPreviewResponse>(`/api/jobs/preview?${q}`)
  },

  listClips: (params?: { job_id?: string; min_score?: number; search?: string }) => {
    const q = new URLSearchParams()
    if (params?.job_id) q.set('job_id', params.job_id)
    if (params?.min_score != null) q.set('min_score', String(params.min_score))
    if (params?.search) q.set('search', params.search)
    return apiFetch<ClipRecord[]>(`/api/clips?${q}`)
  },

  getClip: (clipId: string) =>
    apiFetch<ClipRecord>(`/api/clips/${clipId}`),

  likeClip: (clipId: string) =>
    apiFetch<ClipRecord>(`/api/clips/${clipId}/like`, { method: 'PATCH' }),

  dislikeClip: (clipId: string) =>
    apiFetch<ClipRecord>(`/api/clips/${clipId}/dislike`, { method: 'PATCH' }),

  rerenderClip: (clipId: string, body: { reframe_provider?: string }) =>
    apiFetch<{ status: string; clip_id: string }>(`/api/clips/${clipId}/rerender`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  listBrandTemplates: () =>
    apiFetch<BrandTemplate[]>('/api/brand-templates'),

  createBrandTemplate: (data: Partial<BrandTemplate>) =>
    apiFetch<BrandTemplate>('/api/brand-templates', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  updateBrandTemplate: (id: string, data: Partial<BrandTemplate>) =>
    apiFetch<BrandTemplate>(`/api/brand-templates/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  deleteBrandTemplate: (id: string) =>
    apiFetch<void>(`/api/brand-templates/${id}`, { method: 'DELETE' }),

  // ── Wave 1 — clip edits (timeline) ────────────────────────────────────────
  getClipEdit: (clipId: string) =>
    apiFetch<ClipEditState>(`/api/clips/${clipId}/edit`),

  upsertClipEdit: (clipId: string, body: ClipEditUpsert) =>
    apiFetch<ClipEditState>(`/api/clips/${clipId}/edit`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  deleteClipEdit: (clipId: string) =>
    apiFetch<void>(`/api/clips/${clipId}/edit`, { method: 'DELETE' }),

  getClipRenderPlan: (clipId: string) =>
    apiFetch<RenderPlan>(`/api/clips/${clipId}/edit/plan`),

  // ── Wave 1 — AI hook ─────────────────────────────────────────────────────
  generateAiHook: (clipId: string) =>
    apiFetch<{ clip_id: string; hook: string }>(
      `/api/clips/${clipId}/ai-hook`,
      { method: 'POST' },
    ),

  // ── Wave 1 — Speech enhance ──────────────────────────────────────────────
  enhanceSpeech: (clipId: string) =>
    apiFetch<{ clip_id: string; output_path: string; provider: string }>(
      `/api/clips/${clipId}/enhance-speech`,
      { method: 'POST' },
    ),

  // ── Wave 1 — XML export ──────────────────────────────────────────────────
  xmlExportUrl: (clipId: string, format: 'premiere' | 'davinci' = 'premiere') =>
    `${BASE}/api/clips/${clipId}/export.xml?format=${format}`,

  // ── Wave 1 — Reprompt ────────────────────────────────────────────────────
  repromptJob: (jobId: string, body: RepromptRequest) =>
    apiFetch<{
      job_id: string
      status: string
      prompt: string | null
      pipeline_options: Record<string, unknown>
    }>(`/api/jobs/${jobId}/reprompt`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // ── Wave 1 — Social accounts ─────────────────────────────────────────────
  listSocialAccounts: () =>
    apiFetch<SocialAccount[]>('/api/social/accounts'),

  createSocialAccount: (body: SocialAccountCreate) =>
    apiFetch<SocialAccount>('/api/social/accounts', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  deleteSocialAccount: (id: string) =>
    apiFetch<void>(`/api/social/accounts/${id}`, { method: 'DELETE' }),

  // ── Wave 1 — Publish jobs ────────────────────────────────────────────────
  createPublish: (body: PublishCreate) =>
    apiFetch<PublishJob>('/api/social/publish', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getPublish: (id: string) =>
    apiFetch<PublishJob>(`/api/social/publish/${id}`),

  listPublishForClip: (clipId: string) => {
    const q = new URLSearchParams({ clip_id: clipId })
    return apiFetch<PublishJob[]>(`/api/social/publish?${q}`)
  },
}

// ── Wave 1 types ────────────────────────────────────────────────────────────

export interface TimelineItem {
  start: number
  end: number
  [key: string]: unknown
}

export interface TimelineTrack {
  kind: 'video' | 'caption' | 'text-overlay'
  items: TimelineItem[]
}

export interface TimelinePayload {
  tracks: TimelineTrack[]
}

export interface ClipEditState {
  clip_id: string
  timeline: TimelinePayload
  version: number
  created_at: string
  updated_at: string
}

export interface ClipEditUpsert {
  timeline: TimelinePayload
  version?: number
}

export interface RenderPlan {
  duration: number
  video: Array<{ start: number; end: number; src: string; trim_start: number }>
  captions: Array<{ start: number; end: number; text: string; style: string }>
  overlays: Array<{
    start: number
    end: number
    text: string
    x: number
    y: number
    font_size: number
    color: string
  }>
}

export interface RepromptRequest {
  prompt?: string
  length_range?: '0-1m' | '1-3m' | '3-5m' | '5-10m' | '10-15m'
  length_min_seconds?: number
  length_max_seconds?: number
}

export interface SocialAccount {
  id: string
  platform: 'youtube' | 'tiktok' | 'instagram' | 'linkedin' | 'x'
  account_handle: string
  display_name: string | null
  expires_at: string | null
  scopes: string[]
  active: boolean
  created_at: string
}

export interface SocialAccountCreate {
  platform: SocialAccount['platform']
  account_handle: string
  display_name?: string
  access_token: string
  refresh_token?: string
  expires_at?: string
  scopes?: string[]
}

export interface PublishCreate {
  clip_id: string
  social_account_id: string
  title?: string
  description?: string
  hashtags?: string[]
  schedule_at?: string
}

export interface PublishJob {
  id: string
  clip_id: string
  social_account_id: string
  title: string | null
  description: string | null
  hashtags: string[]
  status:
    | 'pending'
    | 'queued'
    | 'posting'
    | 'published'
    | 'failed'
    | 'cancelled'
  schedule_at: string | null
  posted_at: string | null
  external_post_id: string | null
  external_post_url: string | null
  error: string | null
  attempts: number
  created_at: string
}
