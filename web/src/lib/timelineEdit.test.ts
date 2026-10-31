import { describe, expect, it } from 'vitest'
import type { TimelinePayload } from '@/api/client'
import {
  clampToReel,
  itemsOf,
  prepareForSave,
  reelDuration,
  segmentsOf,
  setCaptionStyle,
  setCaptionText,
  setSegments,
  splitAt,
  upgradeTimeline,
} from './timelineEdit'

const WINDOW = { start: 100, end: 110 }

/** The server's default timeline for a 10 s clip (app/services/clip_words.default_timeline). */
const V1: TimelinePayload = {
  tracks: [
    { kind: 'video', items: [{ start: 0, end: 10, src: 'main', trim_start: 0 }] },
    {
      kind: 'caption',
      items: [
        {
          start: 1,
          end: 2,
          text: 'hello there',
          style: 'default',
          words: [
            { word: 'hello', start: 1, end: 1.5 },
            { word: 'there', start: 1.5, end: 2 },
          ],
        },
        { start: 5, end: 6, text: 'later on', style: 'default', words: [] },
      ],
    },
    { kind: 'text-overlay', items: [{ start: 8, end: 9.5, text: 'hook', x: 0.5, y: 0.1 }] },
  ],
}

describe('upgradeTimeline (v1 -> schema v2)', () => {
  it("turns the reel's video item into a source segment of the clip's window", () => {
    const v2 = upgradeTimeline(V1, WINDOW)!

    expect(v2.schema_version).toBe(2)
    expect(itemsOf(v2, 'video')).toEqual([{ start: 100, end: 110, src: 'main' }])
    // Reel-clock items are unchanged when the whole window plays.
    expect(itemsOf(v2, 'caption').map((c) => [c.start, c.end, c.text])).toEqual([
      [1, 2, 'hello there'],
      [5, 6, 'later on'],
    ])
    expect(itemsOf(v2, 'caption')[0].words).toEqual([
      { word: 'hello', start: 1, end: 1.5 },
      { word: 'there', start: 1.5, end: 2 },
    ])
    expect(itemsOf(v2, 'text-overlay')[0]).toMatchObject({ start: 8, end: 9.5, text: 'hook' })
  })

  it('maps trim_start into source seconds', () => {
    const v1: TimelinePayload = { tracks: [{ kind: 'video', items: [{ start: 0, end: 4, src: 'main', trim_start: 3 }] }] }

    expect(itemsOf(upgradeTimeline(v1, WINDOW)!, 'video')).toEqual([{ start: 103, end: 107, src: 'main' }])
  })

  it("uses the whole window for the editor's zero-length placeholder", () => {
    const v1: TimelinePayload = { tracks: [{ kind: 'video', items: [{ start: 0, end: 0, src: 'main' }] }] }

    expect(segmentsOf(upgradeTimeline(v1, WINDOW)!)).toEqual([{ start: 100, end: 110 }])
  })

  it('keeps caption_style and leaves a v2 timeline as is', () => {
    const styled = setCaptionStyle(V1, 'hormozi')
    const v2 = upgradeTimeline(styled, WINDOW)!

    expect(v2.caption_style).toBe('hormozi')
    expect(upgradeTimeline(v2, WINDOW)).toBe(v2)
  })

  it('cannot upgrade without a window or with a foreign video file', () => {
    expect(upgradeTimeline(V1, null)).toBeNull()
    const foreign: TimelinePayload = { tracks: [{ kind: 'video', items: [{ start: 0, end: 5, src: '/x.mp4' }] }] }
    expect(upgradeTimeline(foreign, WINDOW)).toBeNull()
  })
})

describe('setSegments / splitAt (trim)', () => {
  const v2 = upgradeTimeline(V1, WINDOW)!

  it('trimming the head shifts reel-clock items and drops what was cut', () => {
    const trimmed = setSegments(v2, [{ start: 101.5, end: 110 }])

    expect(reelDuration(trimmed)).toBeCloseTo(8.5)
    const caps = itemsOf(trimmed, 'caption')
    // "hello" (1-1.5) is cut away; "there" survives at 0-0.5.
    expect(caps[0]).toMatchObject({ start: 0, end: 0.5, text: 'there' })
    expect(caps[0].words).toEqual([{ word: 'there', start: 0, end: 0.5 }])
    expect(caps[1]).toMatchObject({ start: 3.5, end: 4.5, text: 'later on' })
    expect(itemsOf(trimmed, 'text-overlay')[0]).toMatchObject({ start: 6.5, end: 8 })
  })

  it('cutting a middle part closes the gap', () => {
    const cut = setSegments(v2, [
      { start: 100, end: 103 },
      { start: 107, end: 110 },
    ])

    expect(reelDuration(cut)).toBe(6)
    const caps = itemsOf(cut, 'caption')
    expect(caps.map((c) => c.text)).toEqual(['hello there']) // 5-6 sat in the cut
    expect(itemsOf(cut, 'text-overlay')[0]).toMatchObject({ start: 4, end: 5.5 })
  })

  it('splits the segment under the playhead in two, keeping the reel', () => {
    const split = splitAt(v2, 4)!

    expect(segmentsOf(split)).toEqual([
      { start: 100, end: 104 },
      { start: 104, end: 110 },
    ])
    expect(itemsOf(split, 'caption').map((c) => [c.start, c.end])).toEqual([
      [1, 2],
      [5, 6],
    ])
    expect(splitAt(v2, 0)).toBeNull()
  })
})

describe('caption text and save shape', () => {
  it('edits one caption, keeping its word timings for the server to re-spell', () => {
    const edited = setCaptionText(V1, 0, 'hullo there')
    const cap = itemsOf(edited, 'caption')[0]

    expect(cap.text).toBe('hullo there')
    expect(cap.words).toHaveLength(2)
    expect(itemsOf(edited, 'caption')[1].text).toBe('later on')
  })

  it('prepareForSave upgrades and clamps items past the reel', () => {
    const long: TimelinePayload = {
      schema_version: 2,
      tracks: [
        { kind: 'video', items: [{ start: 100, end: 104, src: 'main' }] },
        { kind: 'text-overlay', items: [{ start: 3, end: 6, text: 'a', x: 0.5, y: 0.1 }, { start: 5, end: 6, text: 'b', x: 0.5, y: 0.1 }] },
      ],
    }

    expect(itemsOf(clampToReel(long), 'text-overlay')).toEqual([{ start: 3, end: 4, text: 'a', x: 0.5, y: 0.1 }])
    expect(prepareForSave(V1, WINDOW).schema_version).toBe(2)
    expect(prepareForSave(V1, null)).toBe(V1)
  })
})
