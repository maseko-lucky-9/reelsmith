import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import type { JobState } from '@/api/client'
import { JobProgressTimeline } from './job-progress-timeline'

function wrap(children: ReactNode) {
  const qc = new QueryClient()
  return render(
    createElement(QueryClientProvider, { client: qc }, children as React.ReactElement),
  )
}

const baseJob: JobState = {
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
  pipeline_options: {
    transcription: true,
    captions: true,
    render: true,
    segment_proposer: true,
    reframe: true,
    broll: true,
    thumbnail: true,
  },
}

describe('<JobProgressTimeline />', () => {
  it('renders all stage labels', () => {
    wrap(<JobProgressTimeline job={baseJob} />)
    // "Prepare workspace" appears in both the row and the live region — getAllByText.
    expect(screen.getAllByText('Prepare workspace').length).toBeGreaterThan(0)
    expect(screen.getByText('Download source')).toBeTruthy()
    expect(screen.getByText('Render reels')).toBeTruthy()
    expect(screen.getByText('Done')).toBeTruthy()
  })

  it('marks the active stage with aria-current="step"', () => {
    wrap(<JobProgressTimeline job={baseJob} />)
    const items = screen.getAllByRole('listitem')
    const active = items.find((el) => el.getAttribute('aria-current') === 'step')
    expect(active).toBeTruthy()
    expect(active!.textContent).toContain('Prepare workspace')
  })

  it('shows N/M for per-chapter stages', () => {
    const job: JobState = {
      ...baseJob,
      destination_folder: '/v',
      video_path: '/v/x.mp4',
      chapters: {
        '0': {
          chapter_index: 0,
          status: 'transcribing',
          clip_path: '/0.mp4',
          audio_path: null,
          transcript: null,
          captions_path: null,
          image_paths: [],
          output_path: null,
          error: null,
        },
        '1': {
          chapter_index: 1,
          status: 'pending',
          clip_path: null,
          audio_path: null,
          transcript: null,
          captions_path: null,
          image_paths: [],
          output_path: null,
          error: null,
        },
      },
    }
    wrap(<JobProgressTimeline job={job} />)
    expect(screen.getByText('1/2')).toBeTruthy()
  })

  it('shows the error message on failed stage', () => {
    const job: JobState = {
      ...baseJob,
      status: 'failed',
      current_step: 'download',
      error: 'network timeout',
    }
    wrap(<JobProgressTimeline job={job} />)
    expect(screen.getByText('network timeout')).toBeTruthy()
  })

  it('has a visually-hidden live region with the active stage label', () => {
    const { container } = wrap(<JobProgressTimeline job={baseJob} />)
    const live = container.querySelector('[role="status"][aria-live="polite"]')
    expect(live).toBeTruthy()
    expect(live!.textContent).toContain('Prepare workspace')
  })

  it('renders skipped row with gray dash icon and helper text', () => {
    const job: JobState = {
      ...baseJob,
      pipeline_options: {
        ...baseJob.pipeline_options,
        transcription: false,
      },
    }
    wrap(<JobProgressTimeline job={job} />)
    // Skipped helper text should appear for transcribe and caption (dependency cascade)
    const skippedTexts = screen.getAllByText('Skipped (per job options)')
    expect(skippedTexts.length).toBeGreaterThanOrEqual(2)
    // Gray dash icon has aria-label="skipped"
    const skippedIcons = screen.getAllByLabelText('skipped')
    expect(skippedIcons.length).toBeGreaterThanOrEqual(2)
  })

  it('live region announces skipped stages on mount', () => {
    const job: JobState = {
      ...baseJob,
      pipeline_options: {
        ...baseJob.pipeline_options,
        transcription: false,
      },
    }
    const { container } = wrap(<JobProgressTimeline job={job} />)
    const live = container.querySelector('[role="status"][aria-live="polite"]')
    expect(live).toBeTruthy()
    expect(live!.textContent).toContain('Stages skipped per job options')
    expect(live!.textContent).toContain('Transcribe audio')
  })
})
