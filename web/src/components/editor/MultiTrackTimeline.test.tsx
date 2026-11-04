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

  it('keeps a v2 timeline\'s voice-over track and top-level keys; its segments are locked', () => {
    const v2: TimelinePayload = {
      schema_version: 2,
      caption_style: 'hormozi',
      mute_source: false,
      tracks: [
        { kind: 'video', items: [{ start: 100, end: 104, src: 'main' }, { start: 108, end: 110, src: 'main' }] },
        { kind: 'caption', items: [{ start: 0, end: 2, text: 'hi' }] },
        { kind: 'text-overlay', items: [] },
        { kind: 'audio', items: [{ start: 0, end: 2, src: 'voiceover_1.wav', gain: 1 }] },
      ],
    }
    const onChange = vi.fn()
    render(<MultiTrackTimeline timeline={v2} duration={6} onChange={onChange} pixelsPerSecond={10} />)

    // Segments are drawn back to back on the reel's clock.
    expect((screen.getByTestId('item-video-1') as HTMLElement).style.left).toBe('40px')
    fireEvent.click(screen.getByTestId('item-video-0'))
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('item-caption-0'))
    const next = onChange.mock.calls[0][0] as TimelinePayload
    expect(next).toMatchObject({ schema_version: 2, caption_style: 'hormozi', mute_source: false })
    expect(next.tracks.find((t) => t.kind === 'audio')!.items).toHaveLength(1)
    expect(next.tracks.find((t) => t.kind === 'video')!.items).toHaveLength(2)
    expect(next.tracks.find((t) => t.kind === 'caption')!.items).toHaveLength(0)
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
