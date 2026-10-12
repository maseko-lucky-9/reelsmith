import { describe, it, expect } from 'vitest'
import type { ChapterArtifacts, JobState } from '@/api/client'
import {
  STAGES,
  deriveOverallProgress,
  deriveStageStates,
  describeActiveStage,
  describeSkippedStages,
  KNOWN_EVENT_TYPES,
  type PipelineEvent,
} from './pipelineStages'

function chapter(i: number, overrides: Partial<ChapterArtifacts> = {}): ChapterArtifacts {
  return {
    chapter_index: i,
    status: 'pending',
    clip_path: null,
    audio_path: null,
    transcript: null,
    captions_path: null,
    image_paths: [],
    output_path: null,
    error: null,
    ...overrides,
  }
}

const ALL_ON_OPTIONS = {
  transcription: true,
  captions: true,
  render: true,
  segment_proposer: true,
  reframe: true,
  broll: true,
  thumbnail: true,
}

function job(overrides: Partial<JobState> = {}): JobState {
  return {
    job_id: 'j1',
    status: 'running',
    current_step: 'folder',
    url: 'https://www.youtube.com/watch?v=x',
    source: 'youtube',
    download_path: '/tmp',
    caption_format: 'srt',
    target_aspect_ratio: 9 / 16,
    destination_folder: null,
    clips_folder: null,
    video_path: null,
    title: null,
    duration: null,
    chapters: {},
    chapters_total: 0,
    chapters_done: 0,
    output_paths: [],
    error: null,
    pipeline_options: { ...ALL_ON_OPTIONS },
    ...overrides,
  }
}

describe('deriveStageStates — fallback when no job yet', () => {
  it('returns all pending with first stage active when job is undefined', () => {
    const result = deriveStageStates(undefined, undefined)
    expect(result).toHaveLength(STAGES.length)
    expect(result[0].state).toBe('active')
    expect(result.slice(1).every((r) => r.state === 'pending')).toBe(true)
  })
})

describe('deriveStageStates — outer stages from JobState', () => {
  it('marks folder done once destination_folder is set', () => {
    const result = deriveStageStates(
      job({ destination_folder: '/tmp/vid', current_step: 'download' }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'folder')?.state).toBe('done')
  })

  it('marks download done once video_path is set', () => {
    const result = deriveStageStates(
      job({ destination_folder: '/tmp/vid', video_path: '/tmp/vid/x.mp4' }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'download')?.state).toBe('done')
  })

  it('marks chapters done once chapters dict has entries', () => {
    const result = deriveStageStates(
      job({
        destination_folder: '/tmp/v',
        video_path: '/tmp/v/x.mp4',
        chapters: { '0': chapter(0) },
      }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'chapters')?.state).toBe('done')
  })

  it('marks folder done when the video is downloaded but the store returns no destination_folder', () => {
    const result = deriveStageStates(job({ current_step: null, video_path: '/tmp/vid/x.mp4' }), [])
    expect(result.find((r) => r.descriptor.id === 'folder')?.state).toBe('done')
    expect(result.find((r) => r.descriptor.id === 'download')?.state).toBe('done')
    expect(result.find((r) => r.descriptor.id === 'chapters')?.state).toBe('active')
  })

  it('marks folder done while the download is reporting progress', () => {
    const result = deriveStageStates(job({ current_step: null }), [
      { type: 'DownloadProgress', payload: { percent: 42 } },
    ])
    expect(result.find((r) => r.descriptor.id === 'folder')?.state).toBe('done')
    expect(result.find((r) => r.descriptor.id === 'download')?.state).toBe('active')
  })

  it('first incomplete stage is active', () => {
    const result = deriveStageStates(
      job({ destination_folder: '/tmp/v' }), // folder done, download next
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'download')?.state).toBe('active')
  })
})

describe('deriveStageStates — per-chapter aggregation', () => {
  it('counts artifacts (and status for extract) across chapters', () => {
    const result = deriveStageStates(
      job({
        destination_folder: '/v',
        video_path: '/v/x.mp4',
        chapters: {
          '0': chapter(0, { status: 'transcribing', transcript: 'hi' }),
          '1': chapter(1, { status: 'extracting' }),
          '2': chapter(2),
        },
      }),
      [],
    )
    const extract = result.find((r) => r.descriptor.id === 'extract')!
    expect(extract.done).toBe(2)
    expect(extract.total).toBe(3)
    expect(extract.state).toBe('active') // partial → first incomplete
    const transcribe = result.find((r) => r.descriptor.id === 'transcribe')!
    expect(transcribe.done).toBe(1)
  })

  it('marks per-chapter stage done when count == chapters_total', () => {
    const result = deriveStageStates(
      job({
        chapters_total: 2,
        chapters: {
          '0': chapter(0, { status: 'transcribing' }),
          '1': chapter(1, { status: 'transcribing' }),
        },
      }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'extract')?.state).toBe('done')
  })

  it('keeps a per-chapter stage open while chapters are still being seeded', () => {
    const result = deriveStageStates(
      job({
        destination_folder: '/v',
        video_path: '/v/x.mp4',
        chapters_total: 5,
        chapters: {
          '0': chapter(0, { status: 'transcribing' }),
          '1': chapter(1, { status: 'transcribing' }),
        },
      }),
      [],
    )
    const extract = result.find((r) => r.descriptor.id === 'extract')!
    expect(extract.total).toBe(5)
    expect(extract.done).toBe(2)
    expect(extract.state).toBe('active')
  })

  it('reads totals and counts from the counters when the SQL store returns no chapters', () => {
    const result = deriveStageStates(
      job({
        current_step: null,
        destination_folder: null,
        video_path: null,
        chapters: {},
        chapters_total: 21,
        chapters_done: 15,
      }),
      [],
    )
    for (const id of ['folder', 'download', 'chapters'] as const) {
      expect(result.find((r) => r.descriptor.id === id)?.state, id).toBe('done')
    }
    expect(result.find((r) => r.descriptor.id === 'chapters')?.total).toBe(21)
    for (const r of result.filter((s) => s.descriptor.perChapter)) {
      expect(r.done, r.descriptor.id).toBeGreaterThanOrEqual(15)
      expect(r.total, r.descriptor.id).toBe(21)
      expect(r.state, r.descriptor.id).not.toBe('done')
    }
    expect(result.find((r) => r.descriptor.id === 'extract')?.state).toBe('active')
  })

  it('events take over when JobState chapters lag', () => {
    const events: PipelineEvent[] = [
      { type: 'ChapterClipExtracted', payload: { chapter_index: 0 } },
      { type: 'ChapterClipExtracted', payload: { chapter_index: 1 } },
    ]
    const result = deriveStageStates(
      job({ chapters: { '0': chapter(0), '1': chapter(1) } }),
      events,
    )
    expect(result.find((r) => r.descriptor.id === 'extract')?.done).toBe(2)
  })

  it('max-merge: events count higher than artifacts is preferred', () => {
    const events: PipelineEvent[] = [
      { type: 'ChapterClipExtracted', payload: { chapter_index: 0 } },
      { type: 'ChapterClipExtracted', payload: { chapter_index: 1 } },
      { type: 'ChapterClipExtracted', payload: { chapter_index: 2 } },
    ]
    const result = deriveStageStates(
      job({
        chapters: {
          '0': chapter(0, { status: 'extracting' }), // only one chapter has progressed
          '1': chapter(1),
          '2': chapter(2),
        },
      }),
      events,
    )
    expect(result.find((r) => r.descriptor.id === 'extract')?.done).toBe(3)
  })

  it('finalise_chapters needs BOTH thumbnail+social events per chapter', () => {
    const events: PipelineEvent[] = [
      { type: 'ThumbnailGenerated', payload: { chapter_index: 0 } },
      { type: 'SocialContentGenerated', payload: { chapter_index: 0 } },
      { type: 'ThumbnailGenerated', payload: { chapter_index: 1 } },
      // chapter 1 missing social — should not count
    ]
    const result = deriveStageStates(
      job({ chapters: { '0': chapter(0), '1': chapter(1) } }),
      events,
    )
    expect(result.find((r) => r.descriptor.id === 'finalise_chapters')?.done).toBe(1)
  })
})

describe('deriveStageStates — extract stage without an intermediate clip (P1)', () => {
  // The backend renders reels straight from the source; ChapterArtifacts.clip_path
  // is never set any more, so the extract row must count chapters by status.
  it('counts extract from chapter status when clip_path is never set', () => {
    const result = deriveStageStates(
      job({
        destination_folder: '/v',
        video_path: '/v/x.mp4',
        chapters: {
          '0': chapter(0, { status: 'transcribing' }),
          '1': chapter(1, { status: 'rendering' }), // transcription off: no 'extracting'
          '2': chapter(2),
        },
      }),
      [],
    )
    const extract = result.find((r) => r.descriptor.id === 'extract')!
    expect(extract.done).toBe(2)
    expect(extract.state).toBe('active')
  })

  it('does not mark extract failed when a chapter fails later in the pipeline', () => {
    const result = deriveStageStates(
      job({
        status: 'failed',
        current_step: 'chapters',
        chapters: {
          '0': chapter(0, { status: 'completed', output_path: '/o0.mp4' }),
          '1': chapter(1, { status: 'failed', error: 'render crashed' }),
        },
      }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'extract')?.state).toBe('done')
  })
})

describe('deriveStageStates — terminal states', () => {
  it('flips every stage to done when status === "completed"', () => {
    const result = deriveStageStates(
      job({ status: 'completed', current_step: 'completed' }),
      [],
    )
    expect(result.every((r) => r.state === 'done')).toBe(true)
  })

  it('marks the failed outer stage when current_step matches', () => {
    const result = deriveStageStates(
      job({ status: 'failed', current_step: 'download', error: 'network down' }),
      [],
    )
    const download = result.find((r) => r.descriptor.id === 'download')!
    expect(download.state).toBe('failed')
    expect(download.error).toBe('network down')
  })

  it('marks per-chapter stage failed when a chapter has status="failed"', () => {
    const result = deriveStageStates(
      job({
        status: 'failed',
        current_step: 'chapters',
        chapters: {
          '0': chapter(0, { status: 'completed' }),
          '1': chapter(1, { status: 'failed', error: 'transcription crashed' }),
        },
      }),
      [],
    )
    // The first incomplete per-chapter stage carries the failure.
    const transcribe = result.find((r) => r.descriptor.id === 'transcribe')!
    expect(transcribe.state).toBe('failed')
    expect(transcribe.error).toBe('transcription crashed')
  })
})

describe('deriveOverallProgress', () => {
  const downloadTick = (percent: unknown): PipelineEvent => ({ type: 'DownloadProgress', payload: { percent } })

  it('download phase has no percent before the first tick', () => {
    const p = deriveOverallProgress(job(), [])
    expect(p.phase).toBe('download')
    expect(p.label).toBe('Downloading source…')
    expect(p.percent).toBeNull()
    expect(p.percentText).toBeNull()
    expect(p.known).toBe(false)
  })

  it('download phase shows the latest DownloadProgress percent', () => {
    const p = deriveOverallProgress(job(), [downloadTick(10), downloadTick(42)])
    expect(p.phase).toBe('download')
    expect(p.percent).toBe(42)
    expect(p.percentText).toBe('42%')
  })

  it('download phase clamps the percent and ignores a non-numeric one', () => {
    expect(deriveOverallProgress(job(), [downloadTick(140)]).percent).toBe(100)
    expect(deriveOverallProgress(job(), [downloadTick(-5)]).percent).toBe(0)
    expect(deriveOverallProgress(job(), [downloadTick('42')]).percent).toBeNull()
  })

  it.each([
    ['video_path is set', job({ video_path: '/v/x.mp4' }), []],
    ['VideoDownloaded arrived', job(), [{ type: 'VideoDownloaded' }]],
  ] as const)('ignores a stale DownloadProgress once %s', (_name, j, extra) => {
    const p = deriveOverallProgress(j, [downloadTick(42), ...extra])
    expect(p.phase).toBe('detecting')
    expect(p.label).toBe('Detecting chapters…')
    expect(p.percent).toBeNull()
    expect(p.percentText).toBeNull()
  })

  it('chapters phase at 0 of 5', () => {
    const p = deriveOverallProgress(job({ chapters_total: 5, chapters_done: 0 }), [])
    expect(p.phase).toBe('chapters')
    expect(p.label).toBe('0 of 5 chapters done · 5 remaining')
    expect(p.percent).toBe(0)
    expect(p.percentText).toBe('0%')
  })

  it('chapters phase at 3 of 10 counts finished chapters only', () => {
    const p = deriveOverallProgress(job({ chapters_total: 10, chapters_done: 3 }), [])
    expect(p).toMatchObject({ known: true, total: 10, done: 3, remaining: 7, failed: false })
    expect(p.label).toBe('3 of 10 chapters done · 7 remaining')
    expect(p.percent).toBe(30)
    expect(p.percentText).toBe('30%')
  })

  it('chapters phase at 10 of 10', () => {
    const p = deriveOverallProgress(job({ chapters_total: 10, chapters_done: 10 }), [])
    expect(p.label).toBe('10 of 10 chapters done · 0 remaining')
    expect(p.percent).toBe(100)
  })

  it('a known chapter total overrides a leftover DownloadProgress', () => {
    const p = deriveOverallProgress(job({ chapters_total: 4, chapters_done: 1 }), [downloadTick(50)])
    expect(p.phase).toBe('chapters')
    expect(p.percent).toBe(25)
  })

  it('clamps chapters_done to the total', () => {
    const p = deriveOverallProgress(job({ chapters_total: 4, chapters_done: 6 }), [])
    expect(p.done).toBe(4)
    expect(p.remaining).toBe(0)
    expect(p.label).toBe('4 of 4 chapters done · 0 remaining')
    expect(p.percent).toBe(100)
  })

  it('treats a missing chapters_total as not yet known', () => {
    const legacy = { ...job({ video_path: '/v/x.mp4' }), chapters_total: undefined } as unknown as JobState
    const p = deriveOverallProgress(legacy, [])
    expect(p.known).toBe(false)
    expect(p.phase).toBe('detecting')
    expect(p.label).toBe('Detecting chapters…')
  })

  it('failed after chapters were known keeps the finished share, in red', () => {
    const p = deriveOverallProgress(
      job({ status: 'failed', chapters_total: 10, chapters_done: 3 }),
      [],
    )
    expect(p.phase).toBe('failed')
    expect(p.failed).toBe(true)
    expect(p.label).toBe('Failed after 3 of 10 chapters')
    expect(p.percent).toBe(30)
  })

  it('failed with an unknown total after the download does not claim chapters were never detected', () => {
    const p = deriveOverallProgress(job({ status: 'failed', video_path: '/v/x.mp4' }), [])
    expect(p.failed).toBe(true)
    expect(p.label).toBe('Failed')
    expect(p.percent).toBeNull()
  })

  it('a pending job is queued, not downloading', () => {
    const p = deriveOverallProgress(job({ status: 'pending' }), [])
    expect(p.phase).toBe('queued')
    expect(p.label).toBe('Queued…')
    expect(p.percent).toBeNull()
  })

  it('a running job that has not downloaded yet is downloading', () => {
    expect(deriveOverallProgress(job({ status: 'running' }), []).label).toBe('Downloading source…')
  })

  it('failed before chapters were detected has no percent', () => {
    const p = deriveOverallProgress(job({ status: 'failed' }), [downloadTick(42)])
    expect(p.phase).toBe('failed')
    expect(p.failed).toBe(true)
    expect(p.label).toBe('Failed before chapters were detected')
    expect(p.percent).toBeNull()
    expect(p.percentText).toBeNull()
  })
})

describe('describeActiveStage', () => {
  it('describes per-chapter active stage with N of M', () => {
    const stages = deriveStageStates(
      job({
        destination_folder: '/v',
        video_path: '/v/x.mp4',
        chapters: {
          '0': chapter(0, { status: 'extracting' }),
          '1': chapter(1, { status: 'extracting' }),
        },
      }),
      [],
    )
    expect(describeActiveStage(stages)).toMatch(/Transcribe audio/i)
  })

  it('reports completion', () => {
    const stages = deriveStageStates(job({ status: 'completed' }), [])
    expect(describeActiveStage(stages)).toBe('Job completed')
  })

  it('reports failure', () => {
    const stages = deriveStageStates(
      job({ status: 'failed', current_step: 'download', error: 'x' }),
      [],
    )
    expect(describeActiveStage(stages)).toMatch(/Failed at/i)
  })
})

describe('KNOWN_EVENT_TYPES', () => {
  it('includes every doneOnEvents from STAGES', () => {
    for (const s of STAGES) {
      for (const t of s.doneOnEvents) {
        expect(KNOWN_EVENT_TYPES.has(t)).toBe(true)
      }
    }
  })

  it('includes JobFailed', () => {
    expect(KNOWN_EVENT_TYPES.has('JobFailed')).toBe(true)
  })

  it('includes StageSkipped', () => {
    expect(KNOWN_EVENT_TYPES.has('StageSkipped')).toBe(true)
  })

  // Drift guard: EventSource cannot wildcard named events, so useJobSSE only hears
  // types it registered a listener for. Every backend EventType must be in this set.
  it('matches the backend EventType enum in app/domain/events.py exactly', async () => {
    // Read the Python source from disk (vitest runs in Node). A computed specifier keeps
    // Node builtins out of the browser tsconfig, and app/ sits outside Vite's fs.allow root.
    const fs = (await import(/* @vite-ignore */ ['node', 'fs'].join(':'))) as {
      readFileSync: (p: string, enc: 'utf8') => string
    }
    const here = (import.meta as ImportMeta & { dirname: string }).dirname
    const eventsPath = `${here}/../../../app/domain/events.py`
    const eventsPy = fs.readFileSync(eventsPath, 'utf8')
    const enumBody = eventsPy.split('class EventType')[1].split(/\n\S/)[0]
    const backend = new Set([...enumBody.matchAll(/^\s+[A-Z_]+\s*=\s*"([^"]+)"/gm)].map((m) => m[1]))
    expect(backend.size).toBeGreaterThan(20)
    expect([...KNOWN_EVENT_TYPES].sort()).toEqual([...backend].sort())
  })
})

describe('deriveStageStates — skipped pinning from pipeline_options', () => {
  it('pins transcribe to skipped when pipeline_options.transcription is false', () => {
    const result = deriveStageStates(
      job({ pipeline_options: { ...ALL_ON_OPTIONS, transcription: false } }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'transcribe')?.state).toBe('skipped')
  })

  it('pins caption to skipped when pipeline_options.captions is false', () => {
    const result = deriveStageStates(
      job({ pipeline_options: { ...ALL_ON_OPTIONS, captions: false } }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'caption')?.state).toBe('skipped')
  })

  it('auto-skips caption when transcription is off (dependency cascade)', () => {
    const result = deriveStageStates(
      job({ pipeline_options: { ...ALL_ON_OPTIONS, transcription: false, captions: true } }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'transcribe')?.state).toBe('skipped')
    expect(result.find((r) => r.descriptor.id === 'caption')?.state).toBe('skipped')
  })

  it('pins extract, render, and finalise_chapters to skipped when render is off', () => {
    const result = deriveStageStates(
      job({ pipeline_options: { ...ALL_ON_OPTIONS, render: false } }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'extract')?.state).toBe('skipped')
    expect(result.find((r) => r.descriptor.id === 'render')?.state).toBe('skipped')
    expect(result.find((r) => r.descriptor.id === 'finalise_chapters')?.state).toBe('skipped')
  })

  it('pins chapters to skipped when segment_proposer is off', () => {
    const result = deriveStageStates(
      job({ pipeline_options: { ...ALL_ON_OPTIONS, segment_proposer: false } }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'chapters')?.state).toBe('skipped')
  })

  it('all-off scenario: only folder, download, export, and complete are live', () => {
    const allOff = {
      transcription: false,
      captions: false,
      render: false,
      segment_proposer: false,
      reframe: false,
      broll: false,
      thumbnail: false,
    }
    const result = deriveStageStates(
      job({ pipeline_options: allOff }),
      [],
    )
    const liveStages = result.filter((r) => r.state !== 'skipped').map((r) => r.descriptor.id)
    expect(liveStages).toContain('folder')
    expect(liveStages).toContain('download')
    expect(liveStages).toContain('export')
    expect(liveStages).toContain('complete')
    // Middle stages should all be skipped
    const skippedStages = result.filter((r) => r.state === 'skipped').map((r) => r.descriptor.id)
    expect(skippedStages).toContain('chapters')
    expect(skippedStages).toContain('extract')
    expect(skippedStages).toContain('transcribe')
    expect(skippedStages).toContain('caption')
    expect(skippedStages).toContain('render')
    expect(skippedStages).toContain('finalise_chapters')
  })

  it('skipped stages stay skipped when job completes (not flipped to done)', () => {
    const result = deriveStageStates(
      job({
        status: 'completed',
        current_step: 'completed',
        pipeline_options: { ...ALL_ON_OPTIONS, transcription: false },
      }),
      [],
    )
    const transcribe = result.find((r) => r.descriptor.id === 'transcribe')!
    expect(transcribe.state).toBe('skipped')
    const caption = result.find((r) => r.descriptor.id === 'caption')!
    expect(caption.state).toBe('skipped')
    // Non-skipped stages should be done
    const folder = result.find((r) => r.descriptor.id === 'folder')!
    expect(folder.state).toBe('done')
  })

  it('skipped pinning overrides event-derived state', () => {
    const events: PipelineEvent[] = [
      { type: 'ChapterTranscribed', payload: { chapter_index: 0 } },
    ]
    const result = deriveStageStates(
      job({
        pipeline_options: { ...ALL_ON_OPTIONS, transcription: false },
        chapters: { '0': chapter(0) },
      }),
      events,
    )
    // Even with a transcription event, stage should be skipped
    expect(result.find((r) => r.descriptor.id === 'transcribe')?.state).toBe('skipped')
  })
})

describe('describeSkippedStages', () => {
  it('returns empty string when no stages are skipped', () => {
    const stages = deriveStageStates(job(), [])
    expect(describeSkippedStages(stages)).toBe('')
  })

  it('lists skipped stage labels when stages are skipped', () => {
    const stages = deriveStageStates(
      job({ pipeline_options: { ...ALL_ON_OPTIONS, transcription: false } }),
      [],
    )
    const desc = describeSkippedStages(stages)
    expect(desc).toContain('Transcribe audio')
    expect(desc).toContain('Generate captions')
    expect(desc).toMatch(/^Stages skipped per job options:/)
  })
})
