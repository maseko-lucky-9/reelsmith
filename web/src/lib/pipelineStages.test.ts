import { describe, it, expect } from 'vitest'
import type { ChapterArtifacts, JobState } from '@/api/client'
import {
  STAGES,
  deriveStageStates,
  describeActiveStage,
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
    output_paths: [],
    error: null,
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

  it('first incomplete stage is active', () => {
    const result = deriveStageStates(
      job({ destination_folder: '/tmp/v' }), // folder done, download next
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'download')?.state).toBe('active')
  })
})

describe('deriveStageStates — per-chapter aggregation', () => {
  it('counts artifacts across chapters', () => {
    const result = deriveStageStates(
      job({
        destination_folder: '/v',
        video_path: '/v/x.mp4',
        chapters: {
          '0': chapter(0, { clip_path: '/c0.mp4', transcript: 'hi' }),
          '1': chapter(1, { clip_path: '/c1.mp4' }),
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

  it('marks per-chapter stage done when count == total', () => {
    const result = deriveStageStates(
      job({
        chapters: {
          '0': chapter(0, { clip_path: '/a' }),
          '1': chapter(1, { clip_path: '/b' }),
        },
      }),
      [],
    )
    expect(result.find((r) => r.descriptor.id === 'extract')?.state).toBe('done')
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
          '0': chapter(0, { clip_path: '/a' }), // only one chapter has artifact
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
          '0': chapter(0, { clip_path: '/a', status: 'completed' }),
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

describe('describeActiveStage', () => {
  it('describes per-chapter active stage with N of M', () => {
    const stages = deriveStageStates(
      job({
        destination_folder: '/v',
        video_path: '/v/x.mp4',
        chapters: {
          '0': chapter(0, { clip_path: '/a' }),
          '1': chapter(1, { clip_path: '/b' }),
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
})
