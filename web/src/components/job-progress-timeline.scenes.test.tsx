import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { JobState } from '@/api/client'
import type { PipelineEvent } from '@/lib/pipelineStages'
import { JobProgressTimeline } from './job-progress-timeline'

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

function renderWith(events: PipelineEvent[]) {
  const qc = new QueryClient()
  qc.setQueryData(['job-events', job.job_id], events)
  render(
    <QueryClientProvider client={qc}>
      <JobProgressTimeline job={job} />
    </QueryClientProvider>,
  )
}

describe('<JobProgressTimeline /> scene indexing (spec 014)', () => {
  it('shows "Indexing scenes n/m" on the active chapters row and the progress bar', () => {
    renderWith([
      { type: 'VideoDownloaded' },
      { type: 'SceneIndexProgress', payload: { done: 7, total: 47 } },
    ])
    const active = screen.getAllByRole('listitem').find((el) => el.getAttribute('aria-current') === 'step')!
    expect(within(active).getByText('Detect chapters')).toBeTruthy()
    expect(within(active).getByText('Indexing scenes 7/47')).toBeTruthy()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('15')
  })

  it('drops the line once the index is ready', () => {
    renderWith([
      { type: 'SceneIndexProgress', payload: { done: 47, total: 47 } },
      { type: 'ScenesIndexed', payload: { chunks: 47, reused: false } },
    ])
    expect(screen.queryByText(/Indexing scenes/)).toBeNull()
  })
})
