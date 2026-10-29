import { describe, it, expect } from 'vitest'
import type { JobState } from '@/api/client'
import {
  KNOWN_EVENT_TYPES,
  deriveOverallProgress,
  sceneIndexLabel,
  sceneIndexProgress,
  type PipelineEvent,
} from './pipelineStages'

const job: JobState = {
  job_id: 'j1',
  status: 'running',
  current_step: 'chapters',
  url: 'https://www.youtube.com/watch?v=x',
  source: 'youtube',
  download_path: '/tmp',
  caption_format: 'srt',
  target_aspect_ratio: 9 / 16,
  destination_folder: '/tmp/x',
  clips_folder: null,
  video_path: '/tmp/x/source.mp4',
  title: 'Talk',
  duration: 372,
  chapters: {},
  chapters_total: 0,
  chapters_done: 0,
  output_paths: [],
  error: null,
  pipeline_options: {
    transcription: true,
    captions: true,
    render: true,
    segment_proposer: true,
    reframe: false,
    broll: false,
    thumbnail: true,
  },
}

const progress = (done: number, total: number): PipelineEvent => ({
  type: 'SceneIndexProgress',
  payload: { done, total },
})

describe('scene indexing progress (spec 014)', () => {
  it('is the latest SceneIndexProgress until ScenesIndexed', () => {
    expect(sceneIndexProgress([])).toBeNull()
    expect(sceneIndexProgress([progress(0, 47), progress(12, 47)])).toEqual({ done: 12, total: 47 })
    expect(sceneIndexProgress([progress(12, 47), { type: 'ScenesIndexed', payload: { chunks: 47 } }])).toBeNull()
    expect(sceneIndexProgress([progress(3, 0), { type: 'SceneIndexProgress', payload: {} }])).toBeNull()
    // A failed index falls back without ScenesIndexed: the line must not stay.
    expect(
      sceneIndexProgress([progress(3, 47), { type: 'StageSkipped', payload: { stage_id: 'scene_search' } }]),
    ).toBeNull()
    expect(
      sceneIndexProgress([progress(3, 47), { type: 'StageSkipped', payload: { stage_id: 'thumbnail' } }]),
    ).toEqual({ done: 3, total: 47 })
    expect(sceneIndexLabel({ done: 12, total: 47 })).toBe('Indexing scenes 12/47')
  })

  it('drives the main progress bar while chapters are not known yet', () => {
    const p = deriveOverallProgress(job, [progress(12, 48)])
    expect(p.label).toBe('Indexing scenes 12/48')
    expect(p.percent).toBe(25)
    expect(p.percentText).toBe('25%')

    const after = deriveOverallProgress(job, [progress(48, 48), { type: 'ScenesIndexed' }])
    expect(after.label).toBe('Detecting chapters…')
    expect(after.percent).toBeNull()
  })

  it('both events are known to the SSE listener', () => {
    expect(KNOWN_EVENT_TYPES.has('SceneIndexProgress')).toBe(true)
    expect(KNOWN_EVENT_TYPES.has('ScenesIndexed')).toBe(true)
  })
})
