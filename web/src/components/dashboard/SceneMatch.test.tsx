import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { ClipRecord } from '@/api/client'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a className={className}>{children}</a>
  ),
  useNavigate: () => vi.fn(),
}))

vi.mock('@/api/client', () => ({
  api: {
    likeClip: vi.fn(),
    dislikeClip: vi.fn(),
    generateAiHook: vi.fn(),
    enhanceSpeech: vi.fn(),
    xmlExportUrl: () => '#',
  },
}))

import { ClipCard } from './ClipCard'
import { ClipListRow } from './ClipListRow'
import { sceneMatch } from './SceneMatch'

function clip(scoreBreakdown: Record<string, unknown> | null): ClipRecord {
  return {
    clip_id: 'clip-1',
    job_id: 'job-1',
    chapter_id: null,
    start: 262.6,
    end: 290.4,
    output_path: '/out/clip-1.mp4',
    thumbnail_path: null,
    title: 'Satellite dishes at dusk',
    summary: null,
    virality_score: 84,
    score_breakdown: scoreBreakdown as ClipRecord['score_breakdown'],
    transcript: null,
    retired: false,
    liked: false,
    disliked: false,
  }
}

const JUDGED = { visual: 0.78, judge: 0.9, emotion: 'joy', reason: 'A row of white dishes at dusk.' }

function withQuery(children: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>)
}

describe('scene-search judge on clip cards (spec 014)', () => {
  it('ClipCard shows the emotion chip and the judge reason', () => {
    withQuery(<ClipCard clip={clip(JUDGED)} jobId="job-1" />)
    expect(screen.getByTestId('emotion-chip').textContent).toBe('joy')
    expect(screen.getByText('A row of white dishes at dusk.')).toBeTruthy()
  })

  it('ClipListRow shows them too', () => {
    withQuery(<ClipListRow clip={clip(JUDGED)} rank={1} jobId="job-1" />)
    expect(screen.getByTestId('emotion-chip').textContent).toBe('joy')
    expect(screen.getByText('A row of white dishes at dusk.')).toBeTruthy()
  })

  it('shows nothing for a clip the judge did not describe', () => {
    withQuery(<ClipCard clip={clip({ hook: 0.6, value: 0.2 })} jobId="job-1" />)
    expect(screen.queryByTestId('emotion-chip')).toBeNull()
  })

  it('ignores non-text and blank values', () => {
    expect(sceneMatch(clip({ emotion: 3, reason: '   ' }))).toEqual({ emotion: null, reason: null })
    expect(sceneMatch(clip(null))).toEqual({ emotion: null, reason: null })
  })
})
