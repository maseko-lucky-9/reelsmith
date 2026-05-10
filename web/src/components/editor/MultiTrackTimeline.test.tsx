/** Smoke + interaction tests for MultiTrackTimeline (W1.12). */
import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { MultiTrackTimeline } from './MultiTrackTimeline'
import type { TimelinePayload } from '@/api/client'

const TIMELINE: TimelinePayload = {
  tracks: [
    {
      kind: 'video',
      items: [{ start: 0, end: 12, src: 'main' }],
    },
    {
      kind: 'caption',
      items: [{ start: 0, end: 6, text: 'hi' }],
    },
    {
      kind: 'text-overlay',
      items: [{ start: 1, end: 4, text: 'hook', x: 0.5, y: 0.1 }],
    },
  ],
}

describe('MultiTrackTimeline', () => {
  it('renders all three tracks and their items', () => {
    render(
      <MultiTrackTimeline
        timeline={TIMELINE}
        duration={12}
        onChange={() => {}}
      />,
    )
    expect(screen.getByTestId('multi-track-timeline')).toBeTruthy()
    expect(screen.getByTestId('item-video-0')).toBeTruthy()
    expect(screen.getByTestId('item-caption-0')).toBeTruthy()
    expect(screen.getByTestId('item-text-overlay-0')).toBeTruthy()
  })

  it('clicking an item removes it via onChange', () => {
    const onChange = vi.fn()
    render(
      <MultiTrackTimeline
        timeline={TIMELINE}
        duration={12}
        onChange={onChange}
      />,
    )
    fireEvent.click(screen.getByTestId('item-text-overlay-0'))
    expect(onChange).toHaveBeenCalledTimes(1)
    const next = onChange.mock.calls[0][0] as TimelinePayload
    const overlayTrack = next.tracks.find((t) => t.kind === 'text-overlay')!
    expect(overlayTrack.items).toHaveLength(0)
    // Other tracks preserved.
    expect(next.tracks.find((t) => t.kind === 'video')!.items).toHaveLength(1)
    expect(next.tracks.find((t) => t.kind === 'caption')!.items).toHaveLength(1)
  })

  it('positions items proportional to pixelsPerSecond', () => {
    render(
      <MultiTrackTimeline
        timeline={TIMELINE}
        duration={12}
        onChange={() => {}}
        pixelsPerSecond={100}
      />,
    )
    const overlay = screen.getByTestId('item-text-overlay-0') as HTMLElement
    expect(overlay.style.left).toBe('100px')
    expect(overlay.style.width).toBe('300px')
  })

  it('respects an empty timeline', () => {
    render(
      <MultiTrackTimeline
        timeline={{ tracks: [] }}
        duration={0}
        onChange={() => {}}
      />,
    )
    expect(screen.getByTestId('multi-track-timeline')).toBeTruthy()
  })
})
