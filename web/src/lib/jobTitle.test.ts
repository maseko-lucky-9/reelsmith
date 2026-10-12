import { describe, it, expect } from 'vitest'
import { jobDisplayTitle, jobStatusLabel } from './jobTitle'

const base = { title: null, status: 'completed', url: 'https://www.youtube.com/watch?v=abc123' } as const

describe('jobDisplayTitle', () => {
  it('uses the title once the download has set it', () => {
    expect(jobDisplayTitle({ ...base, title: 'My video' })).toBe('My video')
  })

  it.each(['pending', 'running'] as const)('shows a placeholder while the job is %s', (status) => {
    expect(jobDisplayTitle({ ...base, status })).toBe('Loading title…')
  })

  it.each(['completed', 'failed'] as const)(
    'never leaves "Loading title…" on a %s job without a title',
    (status) => {
      expect(jobDisplayTitle({ ...base, status })).toBe('youtube.com/watch?v=abc123')
    },
  )

  it('treats a blank title as missing', () => {
    expect(jobDisplayTitle({ ...base, title: '   ' })).toBe('youtube.com/watch?v=abc123')
  })

  it('does not show a server path for an uploaded file', () => {
    expect(jobDisplayTitle({ ...base, url: 'upload:///var/data/uploads/secret.mp4' })).toBe('Untitled video')
  })

  it('falls back for a URL that cannot be parsed', () => {
    expect(jobDisplayTitle({ ...base, url: 'not a url' })).toBe('Untitled video')
  })
})

describe('jobStatusLabel', () => {
  it.each([
    ['pending', 'Queued'],
    ['running', 'Processing…'],
    ['completed', 'Completed'],
    ['failed', 'Failed'],
  ] as const)('%s -> %s', (status, label) => {
    expect(jobStatusLabel(status)).toBe(label)
  })
})
