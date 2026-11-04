/**
 * timelineEdit helpers not covered by timelineEdit.test.ts: sourceAt, the
 * item helpers, audio remapping on trim/split, an edited caption trimmed,
 * and upgradeTimeline edge cases.
 */
import { describe, it, expect } from 'vitest'
import type { TimelineItem, TimelinePayload } from '@/api/client'
import {
  addItem,
  itemsOf,
  removeItem,
  setCaptionStyle,
  setSegments,
  sourceAt,
  splitAt,
  upgradeTimeline,
} from './timelineEdit'

function v2(segments: Array<[number, number]>, extra: TimelinePayload['tracks'] = []): TimelinePayload {
  return {
    schema_version: 2,
    tracks: [{ kind: 'video', items: segments.map(([start, end]) => ({ start, end, src: 'main' })) }, ...extra],
  }
}

const span = (it: TimelineItem) => [it.start, it.end]

describe('sourceAt', () => {
  const tl = v2([
    [10, 20],
    [30, 35],
  ])

  it('maps reel time through the segments played back to back', () => {
    expect(sourceAt(tl, 5)).toBe(15)
    expect(sourceAt(tl, 12)).toBe(32)
  })

  it('reads a segment boundary as the start of the next segment', () => {
    expect(sourceAt(tl, 10)).toBe(30)
  })

  it('reads the reel end as the last segment end, and null past it', () => {
    expect(sourceAt(tl, 15)).toBe(35)
    expect(sourceAt(tl, 16)).toBeNull()
  })

  it('is null for a v1 timeline', () => {
    expect(sourceAt({ tracks: [{ kind: 'video', items: [{ start: 0, end: 10 }] }] }, 5)).toBeNull()
  })
})

describe('item helpers', () => {
  it('setCaptionStyle(null) deletes the key without touching the input', () => {
    const tl: TimelinePayload = { ...v2([[0, 10]]), caption_style: 'hormozi' }
    const next = setCaptionStyle(tl, null)
    expect('caption_style' in next).toBe(false)
    expect(tl.caption_style).toBe('hormozi')
    expect(setCaptionStyle(next, 'mrbeast').caption_style).toBe('mrbeast')
  })

  it('addItem inserts in start order and adds a missing track', () => {
    const tl = v2(
      [[0, 10]],
      [
        {
          kind: 'text-overlay',
          items: [
            { start: 0, end: 1, text: 'a' },
            { start: 5, end: 6, text: 'c' },
          ],
        },
      ],
    )
    const next = addItem(tl, 'text-overlay', { start: 3, end: 4, text: 'b' })
    expect(itemsOf(next, 'text-overlay').map((it) => it.text)).toEqual(['a', 'b', 'c'])
    expect(itemsOf(tl, 'text-overlay')).toHaveLength(2)

    const withAudio = addItem(tl, 'audio', { start: 1, end: 2, src: 'vo.wav' })
    expect(withAudio.tracks.map((t) => t.kind)).toEqual(['video', 'text-overlay', 'audio'])
  })

  it('removeItem drops only the item at the index', () => {
    const tl = v2(
      [[0, 10]],
      [
        {
          kind: 'text-overlay',
          items: [
            { start: 0, end: 1, text: 'a' },
            { start: 2, end: 3, text: 'b' },
            { start: 4, end: 5, text: 'c' },
          ],
        },
      ],
    )
    expect(itemsOf(removeItem(tl, 'text-overlay', 1), 'text-overlay').map((it) => it.text)).toEqual(['a', 'c'])
    expect(itemsOf(tl, 'text-overlay')).toHaveLength(3)
  })
})

describe('audio items follow trims and splits', () => {
  const withVoice = (items: TimelineItem[]) => v2([[100, 110]], [{ kind: 'audio', items }])
  const audio = (tl: TimelinePayload) => itemsOf(tl, 'audio').map(span)

  it('shifts a voice-over with the source under its start, keeping its length', () => {
    const tl = withVoice([{ start: 2, end: 4, src: 'vo.wav', gain: 1 }])
    const next = setSegments(tl, [{ start: 101, end: 110 }])
    expect(audio(next)).toEqual([[1, 3]])
    expect(itemsOf(next, 'audio')[0]).toMatchObject({ src: 'vo.wav', gain: 1 })
  })

  it('keeps a voice-over whole when a cut falls under its tail (unlike a caption)', () => {
    const tl = withVoice([{ start: 3, end: 6, src: 'vo.wav' }])
    const cut = setSegments(tl, [
      { start: 100, end: 105 },
      { start: 107, end: 110 },
    ])
    expect(audio(cut)).toEqual([[3, 6]])
  })

  it('drops a voice-over whose start is cut away', () => {
    const tl = withVoice([{ start: 2, end: 4, src: 'vo.wav' }])
    expect(audio(setSegments(tl, [{ start: 103, end: 110 }]))).toEqual([])
  })

  it("truncates a voice-over at the edited reel's end", () => {
    const tl = withVoice([{ start: 6, end: 9, src: 'vo.wav' }])
    expect(audio(setSegments(tl, [{ start: 100, end: 108 }]))).toEqual([[6, 8]])
  })

  it('keeps voice-overs in place on a split, then remaps them when a piece is deleted', () => {
    const tl = withVoice([
      { start: 2, end: 4, src: 'a.wav' },
      { start: 6, end: 9, src: 'b.wav' },
    ])
    const split = splitAt(tl, 5)
    expect(split).not.toBeNull()
    expect(audio(split!)).toEqual([
      [2, 4],
      [6, 9],
    ])
    const deleted = setSegments(split!, [{ start: 105, end: 110 }])
    expect(itemsOf(deleted, 'audio').map((it) => [it.src, it.start, it.end])).toEqual([['b.wav', 1, 4]])
  })
})

describe('an edited caption whose word count changed, then trimmed', () => {
  it('keeps the edited text whole and only the surviving word timings', () => {
    const tl = v2(
      [[100, 110]],
      [
        {
          kind: 'caption',
          items: [
            {
              start: 1,
              end: 2,
              text: 'hullo there friend',
              words: [
                { word: 'hello', start: 1, end: 1.5 },
                { word: 'there', start: 1.5, end: 2 },
              ],
            },
          ],
        },
      ],
    )
    const caption = itemsOf(setSegments(tl, [{ start: 101.5, end: 110 }]), 'caption')
    expect(caption).toEqual([
      { start: 0, end: 0.5, text: 'hullo there friend', words: [{ word: 'there', start: 0, end: 0.5 }] },
    ])
  })
})

describe('upgradeTimeline edge cases', () => {
  it('turns several v1 video items into ordered source segments and remaps the captions', () => {
    const v1: TimelinePayload = {
      tracks: [
        {
          kind: 'video',
          items: [
            { start: 5, end: 10, src: 'main', trim_start: 8 },
            { start: 0, end: 5, src: 'main', trim_start: 0 },
          ],
        },
        {
          kind: 'caption',
          items: [
            { start: 4, end: 6, text: 'across' },
            { start: 6, end: 7, text: 'second' },
          ],
        },
      ],
    }
    const up = upgradeTimeline(v1, { start: 100, end: 120 })
    expect(up?.schema_version).toBe(2)
    expect(itemsOf(up!, 'video')).toEqual([
      { start: 100, end: 105, src: 'main' },
      { start: 108, end: 113, src: 'main' },
    ])
    expect(itemsOf(up!, 'caption').map(span)).toEqual([
      [4, 6],
      [6, 7],
    ])
  })

  it('falls back to the whole window when trim_start is beyond it', () => {
    const v1: TimelinePayload = {
      tracks: [{ kind: 'video', items: [{ start: 0, end: 5, src: 'main', trim_start: 12 }] }],
    }
    expect(itemsOf(upgradeTimeline(v1, { start: 100, end: 110 })!, 'video')).toEqual([
      { start: 100, end: 110, src: 'main' },
    ])
  })

  it('cuts an item at the window end when trim_start leaves less than its length', () => {
    const v1: TimelinePayload = {
      tracks: [{ kind: 'video', items: [{ start: 0, end: 5, src: 'main', trim_start: 8 }] }],
    }
    expect(itemsOf(upgradeTimeline(v1, { start: 100, end: 110 })!, 'video')).toEqual([
      { start: 108, end: 110, src: 'main' },
    ])
  })
})
