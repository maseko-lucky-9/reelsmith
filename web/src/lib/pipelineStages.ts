/**
 * Pure helpers for deriving job-pipeline progress from JobState + SSE events.
 *
 * Source-of-truth pairing:
 *   - Backend EventType enum: app/domain/events.py:11-29
 *   - ChapterStatus ladder:   app/domain/models.py:9-17
 *   - Orchestrator emits:     app/workers/orchestrator.py:67+
 *
 * If the backend adds an EventType, the runtime in `useJobSSE` will warn for
 * unknown events; add the new stage here to surface it.
 */
import type { ChapterArtifacts, ChapterStatus, JobState } from '@/api/client'

export type StageId =
  | 'folder'
  | 'download'
  | 'chapters'
  | 'extract'
  | 'transcribe'
  | 'caption'
  | 'render'
  | 'finalise_chapters'
  | 'export'
  | 'complete'

export type StageState = 'pending' | 'active' | 'done' | 'failed'

export interface StageDescriptor {
  id: StageId
  label: string
  /** EventType strings that mark this stage as fully done (or, for per-chapter stages, increment the counter). */
  doneOnEvents: readonly string[]
  /** True if this stage repeats per chapter (sub-progress N/M). */
  perChapter?: boolean
  /** ChapterArtifacts field whose non-null value means "this chapter has cleared this stage". */
  artifactField?: keyof ChapterArtifacts | null
}

export const STAGES: readonly StageDescriptor[] = [
  { id: 'folder',     label: 'Prepare workspace',     doneOnEvents: ['FolderCreated'] },
  { id: 'download',   label: 'Download source',       doneOnEvents: ['VideoDownloaded'] },
  { id: 'chapters',   label: 'Detect chapters',       doneOnEvents: ['ChaptersDetected'] },
  { id: 'extract',    label: 'Extract clips',         doneOnEvents: ['ChapterClipExtracted'],  perChapter: true, artifactField: 'clip_path' },
  { id: 'transcribe', label: 'Transcribe audio',      doneOnEvents: ['ChapterTranscribed'],    perChapter: true, artifactField: 'transcript' },
  { id: 'caption',    label: 'Generate captions',     doneOnEvents: ['CaptionsGenerated'],     perChapter: true, artifactField: 'captions_path' },
  { id: 'render',     label: 'Render reels',          doneOnEvents: ['ClipRendered'],          perChapter: true, artifactField: 'output_path' },
  {
    id: 'finalise_chapters',
    label: 'Thumbnails + social',
    doneOnEvents: ['ThumbnailGenerated', 'SocialContentGenerated'],
    perChapter: true,
    artifactField: null,
  },
  { id: 'export',   label: 'Export & manifest', doneOnEvents: ['ExportCompleted', 'ManifestCreated'] },
  { id: 'complete', label: 'Done',              doneOnEvents: ['JobCompleted'] },
] as const

/** Outer steps the orchestrator writes to JobState.current_step (orchestrator.py:93,117,157,250). */
const STEP_ORDER = ['folder', 'download', 'chapters', 'completed'] as const

function isStepPast(current: string | null | undefined, target: string): boolean {
  if (!current) return false
  const ci = STEP_ORDER.indexOf(current as (typeof STEP_ORDER)[number])
  const ti = STEP_ORDER.indexOf(target as (typeof STEP_ORDER)[number])
  if (ci === -1 || ti === -1) return false
  return ci > ti
}

export interface PipelineEvent {
  type: string
  payload?: { chapter_index?: number } & Record<string, unknown>
}

export interface DerivedStage {
  descriptor: StageDescriptor
  state: StageState
  /** For per-chapter stages: how many chapters have cleared this stage. */
  done: number
  /** Total chapters when known; null if chapters not yet detected. */
  total: number | null
  /** Failure message when state === 'failed'. */
  error: string | null
}

/** Count chapters whose ChapterStatus has advanced past the given stage. */
const STATUS_RANK: Record<ChapterStatus, number> = {
  pending: 0,
  extracting: 1,
  transcribing: 2,
  captioning: 3,
  rendering: 4,
  completed: 5,
  failed: 5, // failed is terminal — counts as "advanced" for stages it cleared
}

const STAGE_TO_STATUS_THRESHOLD: Partial<Record<StageId, ChapterStatus>> = {
  extract: 'extracting',
  transcribe: 'transcribing',
  caption: 'captioning',
  render: 'rendering',
  finalise_chapters: 'completed',
}

function chaptersDoneFromArtifacts(
  chapters: Record<string, ChapterArtifacts>,
  stage: StageDescriptor,
): number {
  const list = Object.values(chapters)
  if (list.length === 0) return 0
  if (stage.artifactField) {
    return list.filter((c) => c[stage.artifactField as keyof ChapterArtifacts] != null).length
  }
  // No artifact field → fall back to ChapterStatus threshold.
  const threshold = STAGE_TO_STATUS_THRESHOLD[stage.id]
  if (!threshold) return 0
  const need = STATUS_RANK[threshold]
  return list.filter((c) => STATUS_RANK[c.status] >= need).length
}

function chaptersDoneFromEvents(events: PipelineEvent[], stage: StageDescriptor): number {
  const matchingEvents = events.filter((e) => stage.doneOnEvents.includes(e.type))
  // For finalise_chapters: thumbnail + social fire once each per chapter; we want
  // chapters where BOTH have fired. Group by chapter_index and require all events.
  if (stage.doneOnEvents.length > 1) {
    const seenPerChapter = new Map<number, Set<string>>()
    for (const e of matchingEvents) {
      const idx = e.payload?.chapter_index
      if (typeof idx !== 'number') continue
      if (!seenPerChapter.has(idx)) seenPerChapter.set(idx, new Set())
      seenPerChapter.get(idx)!.add(e.type)
    }
    let count = 0
    for (const seen of seenPerChapter.values()) {
      if (stage.doneOnEvents.every((t) => seen.has(t))) count++
    }
    return count
  }
  // Single-event per-chapter stage: distinct chapter_index count.
  const seen = new Set<number>()
  for (const e of matchingEvents) {
    const idx = e.payload?.chapter_index
    if (typeof idx === 'number') seen.add(idx)
  }
  return seen.size
}

function isOuterStageDone(
  stage: StageDescriptor,
  job: JobState,
  events: PipelineEvent[],
): boolean {
  // Cold-mount signal per the hydration table.
  switch (stage.id) {
    case 'folder':
      return Boolean(job.destination_folder) || isStepPast(job.current_step, 'folder')
    case 'download':
      return Boolean(job.video_path) || isStepPast(job.current_step, 'download')
    case 'chapters':
      return Object.keys(job.chapters ?? {}).length > 0 || isStepPast(job.current_step, 'chapters')
    case 'export':
      return (
        (job.output_paths?.length ?? 0) >= Object.keys(job.chapters ?? {}).length &&
        Object.keys(job.chapters ?? {}).length > 0
      ) || stage.doneOnEvents.some((t) => events.some((e) => e.type === t))
    case 'complete':
      return job.status === 'completed'
    default:
      return false
  }
}

/**
 * Derive timeline state from the authoritative JobState plus a stream of recent SSE events.
 * Pure function: no React, no side effects. Easy to unit-test.
 */
export function deriveStageStates(
  job: JobState | undefined,
  events: PipelineEvent[] | undefined,
): DerivedStage[] {
  const evs = events ?? []
  if (!job) {
    // No job yet — render all pending with the first as active so the UI isn't blank.
    return STAGES.map((s, i) => ({
      descriptor: s,
      state: i === 0 ? 'active' : 'pending',
      done: 0,
      total: null,
      error: null,
    }))
  }

  const totalChapters = Object.keys(job.chapters ?? {}).length || null

  // Per-chapter failure detection.
  const failedChapter = Object.values(job.chapters ?? {}).find((c) => c.status === 'failed')

  // First derive raw done state for each stage (max of poll-derived and event-derived).
  const rawStates = STAGES.map((stage): { stage: StageDescriptor; done: boolean; count: number } => {
    if (stage.perChapter) {
      const fromArtifacts = chaptersDoneFromArtifacts(job.chapters ?? {}, stage)
      const fromEvents = chaptersDoneFromEvents(evs, stage)
      const count = Math.max(fromArtifacts, fromEvents)
      const fullyDone = totalChapters !== null && count >= totalChapters && totalChapters > 0
      return { stage, done: fullyDone, count }
    }
    // Outer stage.
    const eventDone = stage.doneOnEvents.some((t) => evs.some((e) => e.type === t))
    const pollDone = isOuterStageDone(stage, job, evs)
    return { stage, done: eventDone || pollDone, count: 0 }
  })

  // Find the first incomplete stage; that's the active one (unless job is terminal).
  const isTerminal = job.status === 'completed' || job.status === 'failed'
  const firstIncomplete = rawStates.findIndex((r) => !r.done)

  return rawStates.map((r, i) => {
    let state: StageState
    if (job.status === 'failed') {
      // Outer failure: the failed stage is r.stage matching current_step (folder/download/chapters).
      // Per-chapter failure: the in-flight per-chapter stage is the failed one.
      const outerFailMatch = r.stage.id === job.current_step
      const perChapterFailMatch = r.stage.perChapter && failedChapter && !r.done
      if (outerFailMatch || perChapterFailMatch) {
        state = 'failed'
      } else {
        state = r.done ? 'done' : 'pending'
      }
    } else if (job.status === 'completed') {
      // Everything flips to done on completion.
      state = 'done'
    } else if (r.done) {
      state = 'done'
    } else if (i === firstIncomplete) {
      state = 'active'
    } else {
      state = 'pending'
    }

    const error =
      state === 'failed'
        ? failedChapter?.error ?? job.error ?? null
        : null

    return {
      descriptor: r.stage,
      state,
      done: r.count,
      total: r.stage.perChapter ? totalChapters : null,
      error,
    }
  })
}

/**
 * Live-region announcement for the active stage transition. Returns null when
 * nothing changed worth announcing.
 */
export function describeActiveStage(stages: DerivedStage[]): string {
  const active = stages.find((s) => s.state === 'active')
  if (active) {
    if (active.descriptor.perChapter && active.total !== null) {
      return `${active.descriptor.label}: ${active.done} of ${active.total}`
    }
    return active.descriptor.label
  }
  const failed = stages.find((s) => s.state === 'failed')
  if (failed) return `Failed at: ${failed.descriptor.label}`
  if (stages.every((s) => s.state === 'done')) return 'Job completed'
  return ''
}

/** Set of all known event types — used by useJobSSE to warn on drift. */
export const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set([
  'VideoRequested',
  ...STAGES.flatMap((s) => [...s.doneOnEvents]),
  'JobFailed',
  'SubtitleImageRendered', // emitted but not rendered as its own row (rolled into render)
])
