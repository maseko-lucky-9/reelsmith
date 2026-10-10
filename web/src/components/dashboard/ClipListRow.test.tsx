import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { ClipRecord } from '@/api/client'

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a className={className}>{children}</a>
  ),
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

import { ClipListRow } from './ClipListRow'

function clip(overrides: Partial<ClipRecord> = {}): ClipRecord {
  return {
    clip_id: 'clip-1',
    job_id: 'job-1',
    chapter_id: null,
    start: 0,
    end: 30,
    output_path: '/out/clip-1.mp4',
    thumbnail_path: null,
    title: 'Ocean talk',
    summary: null,
    virality_score: 80,
    score_breakdown: null,
    transcript: null,
    retired: false,
    liked: false,
    disliked: false,
    ...overrides,
  }
}

function renderRow(c: ClipRecord) {
  const qc = new QueryClient()
  render(
    <QueryClientProvider client={qc}>
      <ClipListRow clip={c} rank={1} jobId="job-1" />
    </QueryClientProvider>,
  )
}

describe('ClipListRow — B-roll credits', () => {
  it("shows the clip's B-roll credits and the Pexels link", () => {
    renderRow(
      clip({
        broll_assets: [
          {
            query: 'ocean',
            start: 3,
            duration: 3,
            provider: 'pexels',
            asset_id: '1234',
            author: 'Jane Doe',
            source_url: 'https://www.pexels.com/video/ocean-1234/',
            path: '/var/cache/1234.mp4',
          },
        ],
      }),
    )

    expect(screen.getByRole('heading', { name: 'B-roll credits' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Jane Doe/ })).toHaveAttribute(
      'href',
      'https://www.pexels.com/video/ocean-1234/',
    )
    expect(screen.getByRole('link', { name: /Videos provided by Pexels/ })).toBeInTheDocument()
  })

  it('shows no credits for a clip without B-roll', () => {
    renderRow(clip({ broll_assets: null }))

    expect(screen.queryByRole('heading', { name: 'B-roll credits' })).toBeNull()
  })
})
