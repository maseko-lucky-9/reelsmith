/**
 * Pure helpers for the clip editor's timeline (spec 016, ADR-014 schema v2).
 *
 * v1 (no `schema_version`): video items are pieces of the rendered reel, on
 * the reel's clock. v2: video items are segments of the source video in
 * source seconds, played back to back; captions, text overlays and audio sit
 * on the edited reel's clock. The server validates either
 * (app/services/timeline_render_service.py); these helpers keep the editor's
 * edits inside those rules.
 */
import type { TimelineItem, TimelinePayload, TimelineTrackKind } from '@/api/client'

/** The clip's window in its source video, seconds (ClipRecord start/end). */
export interface SourceWindow {
  start: number
  end: number
}

export interface Segment {
  start: number
  end: number
}

/** A piece of the reel: `len` seconds at reel time `reel`, read from source time `src`. */
interface Piece {
  reel: number
  src: number
  len: number
}

/** Server minimum segment length (MIN_SEGMENT_SECONDS). */
export const MIN_SEGMENT_SECONDS = 0.05
const EPS = 1e-3

const round3 = (n: number) => Math.round(n * 1000) / 1000

export function isV2(timeline: TimelinePayload): boolean {
  return timeline.schema_version === 2
}

export function itemsOf(timeline: TimelinePayload, kind: TimelineTrackKind): TimelineItem[] {
  return timeline.tracks.find((t) => t.kind === kind)?.items ?? []
}

/** The timeline with the first `kind` track's items replaced (the track is added when missing). */
export function withItems(
  timeline: TimelinePayload,
  kind: TimelineTrackKind,
  items: TimelineItem[],
): TimelinePayload {
  const idx = timeline.tracks.findIndex((t) => t.kind === kind)
  const tracks =
    idx >= 0
      ? timeline.tracks.map((t, i) => (i === idx ? { ...t, items } : t))
      : [...timeline.tracks, { kind, items }]
  return { ...timeline, tracks }
}

/** v2 video items as source segments. */
export function segmentsOf(timeline: TimelinePayload): Segment[] {
  if (!isV2(timeline)) return []
  return itemsOf(timeline, 'video').map((it) => ({ start: Number(it.start), end: Number(it.end) }))
}

/** The edited reel's length (v2: the segments back to back; v1: the last item's end). */
export function reelDuration(timeline: TimelinePayload): number {
  if (isV2(timeline)) return segmentsOf(timeline).reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0)
  let end = 0
  for (const track of timeline.tracks) for (const it of track.items) end = Math.max(end, Number(it.end) || 0)
  return end
}

function piecesFromSegments(segments: Segment[]): Piece[] {
  let cursor = 0
  return segments.map((s) => {
    const piece = { reel: cursor, src: s.start, len: Math.max(0, s.end - s.start) }
    cursor += piece.len
    return piece
  })
}

function pieceAtReel(pieces: Piece[], t: number): Piece | null {
  return (
    pieces.find((p) => t >= p.reel - EPS && t < p.reel + p.len - EPS) ??
    pieces.find((p) => Math.abs(t - (p.reel + p.len)) <= EPS) ??
    null
  )
}

function reelToSource(pieces: Piece[], t: number): number | null {
  const p = pieceAtReel(pieces, t)
  return p ? p.src + Math.min(Math.max(t - p.reel, 0), p.len) : null
}

function sourceToReel(pieces: Piece[], s: number): number | null {
  const p =
    pieces.find((q) => s >= q.src - EPS && s < q.src + q.len - EPS) ??
    pieces.find((q) => Math.abs(s - (q.src + q.len)) <= EPS)
  return p ? p.reel + Math.min(Math.max(s - p.src, 0), p.len) : null
}

/** Source time under reel time `t` of a v2 timeline (null past the reel). */
export function sourceAt(timeline: TimelinePayload, t: number): number | null {
  return reelToSource(piecesFromSegments(segmentsOf(timeline)), t)
}

/**
 * Moves an item on the old reel's clock to the new one, through the source
 * time under it. Null when none of it survives. A part cut away is trimmed
 * off (start or end snaps to the edge of the surviving piece).
 */
function remapSpan(start: number, end: number, from: Piece[], to: Piece[]): Segment | null {
  const map = (t: number) => {
    const s = reelToSource(from, t)
    return s === null ? null : sourceToReel(to, s)
  }
  const a = map(start)
  let b = map(end)
  if (a === null && b === null) return null
  if (a === null) {
    // The head was cut: the item starts where its surviving piece starts.
    const head = pieceAtReel(to, b as number)?.reel ?? null
    if (head === null || (b as number) - head < MIN_SEGMENT_SECONDS) return null
    return { start: round3(head), end: round3(b as number) }
  }
  if (b === null || b <= a) {
    const p = pieceAtReel(to, a)
    b = p ? p.reel + p.len : null
  }
  if (b === null || b - a < MIN_SEGMENT_SECONDS) return null
  return { start: round3(a), end: round3(b) }
}

function remapItems(items: TimelineItem[], kind: TimelineTrackKind, from: Piece[], to: Piece[]): TimelineItem[] {
  const out: TimelineItem[] = []
  const reelEnd = to.reduce((sum, p) => sum + p.len, 0)
  for (const it of items) {
    const start = Number(it.start)
    const end = Number(it.end)
    if (kind === 'audio') {
      // A voice-over keeps its length; it is cut at the reel's end.
      const s = reelToSource(from, start)
      const a = s === null ? null : sourceToReel(to, s)
      if (a === null) continue
      const b = Math.min(a + (end - start), reelEnd)
      if (b - a < MIN_SEGMENT_SECONDS) continue
      out.push({ ...it, start: round3(a), end: round3(b) })
      continue
    }
    const span = remapSpan(start, end, from, to)
    if (!span) continue
    if (kind !== 'caption' || !Array.isArray(it.words)) {
      out.push({ ...it, ...span })
      continue
    }
    const words = it.words as Array<{ word?: string; start: number; end: number }>
    const tokens = String(it.text ?? '').split(/\s+/).filter(Boolean)
    const aligned = tokens.length === words.length
    const keptWords: typeof words = []
    const keptTokens: string[] = []
    words.forEach((w, i) => {
      const ws = remapSpan(Number(w.start), Math.max(Number(w.end), Number(w.start) + MIN_SEGMENT_SECONDS), from, to)
      if (!ws) return
      keptWords.push({ ...w, start: Math.max(ws.start, span.start), end: Math.min(ws.end, span.end) })
      if (aligned) keptTokens.push(tokens[i])
    })
    if (aligned && keptTokens.length === 0) continue
    out.push({
      ...it,
      ...span,
      text: aligned ? keptTokens.join(' ') : it.text,
      words: keptWords,
    })
  }
  return out
}

const REEL_KINDS: TimelineTrackKind[] = ['caption', 'text-overlay', 'audio']

function remapTimeline(timeline: TimelinePayload, from: Piece[], segments: Segment[]): TimelinePayload {
  const to = piecesFromSegments(segments)
  return {
    ...timeline,
    schema_version: 2,
    tracks: timeline.tracks.map((track) => {
      if (track.kind === 'video') {
        return { ...track, items: segments.map((s) => ({ start: round3(s.start), end: round3(s.end), src: 'main' })) }
      }
      if (REEL_KINDS.includes(track.kind)) return { ...track, items: remapItems(track.items, track.kind, from, to) }
      return track
    }),
  }
}

/**
 * The timeline as schema v2. A v2 timeline is returned as is. A v1 timeline's
 * video items (pieces of the rendered reel, which is the clip's source
 * window) become source segments; with no usable item (the editor's
 * zero-length placeholder) the whole window is one segment. Null when it
 * cannot be upgraded: no known window, or a video item reading a file other
 * than the clip (`src` not `main`).
 */
export function upgradeTimeline(timeline: TimelinePayload, window: SourceWindow | null): TimelinePayload | null {
  if (isV2(timeline)) return timeline
  if (!window || !(window.end > window.start) || window.start < 0) return null
  const video = itemsOf(timeline, 'video')
  if (video.some((it) => it.src != null && it.src !== 'main')) return null
  const windowLen = window.end - window.start
  const from: Piece[] = video
    .map((it) => {
      const start = Number(it.start) || 0
      const end = Number(it.end) || 0
      const trim = Math.max(0, Number(it.trim_start) || 0)
      const len = Math.min(end - start, windowLen - trim)
      return { reel: start, src: window.start + trim, len }
    })
    .filter((p) => p.len >= MIN_SEGMENT_SECONDS)
    .sort((a, b) => a.reel - b.reel)
  if (from.length === 0) from.push({ reel: 0, src: window.start, len: windowLen })
  const segments = from.map((p) => ({ start: p.src, end: p.src + p.len }))
  const tracks = timeline.tracks.some((t) => t.kind === 'video')
    ? timeline.tracks
    : [{ kind: 'video' as const, items: [] }, ...timeline.tracks]
  return remapTimeline({ ...timeline, tracks }, from, segments)
}

/** A v2 timeline with new source segments; reel-clock items follow the source they sit on. */
export function setSegments(timeline: TimelinePayload, segments: Segment[]): TimelinePayload {
  return remapTimeline(timeline, piecesFromSegments(segmentsOf(timeline)), segments)
}

/** Splits the v2 segment under reel time `t` in two; null when `t` is too near an edge. */
export function splitAt(timeline: TimelinePayload, t: number): TimelinePayload | null {
  const segments = segmentsOf(timeline)
  const pieces = piecesFromSegments(segments)
  const idx = pieces.findIndex(
    (p) => t > p.reel + MIN_SEGMENT_SECONDS && t < p.reel + p.len - MIN_SEGMENT_SECONDS,
  )
  if (idx < 0) return null
  const cut = round3(pieces[idx].src + (t - pieces[idx].reel))
  const next = [...segments]
  next.splice(idx, 1, { start: segments[idx].start, end: cut }, { start: cut, end: segments[idx].end })
  return setSegments(timeline, next)
}

/** v2: drops (or trims) reel-clock items that run past the edited reel's end. */
export function clampToReel(timeline: TimelinePayload): TimelinePayload {
  if (!isV2(timeline)) return timeline
  const end = reelDuration(timeline)
  return {
    ...timeline,
    tracks: timeline.tracks.map((track) =>
      REEL_KINDS.includes(track.kind)
        ? {
            ...track,
            items: track.items
              .filter((it) => Number(it.start) < end - MIN_SEGMENT_SECONDS)
              .map((it) => (Number(it.end) > end ? { ...it, end: round3(end) } : it)),
          }
        : track,
    ),
  }
}

/** What the editor sends on save: upgraded to v2 when possible, kept inside the reel. */
export function prepareForSave(timeline: TimelinePayload, window: SourceWindow | null): TimelinePayload {
  const upgraded = upgradeTimeline(timeline, window) ?? timeline
  return clampToReel(upgraded)
}

export function setCaptionText(timeline: TimelinePayload, idx: number, text: string): TimelinePayload {
  const items = itemsOf(timeline, 'caption').map((it, i) => (i === idx ? { ...it, text } : it))
  return withItems(timeline, 'caption', items)
}

export function setCaptionStyle(timeline: TimelinePayload, style: string | null): TimelinePayload {
  const next = { ...timeline }
  if (style === null) delete next.caption_style
  else next.caption_style = style
  return next
}

export function addItem(timeline: TimelinePayload, kind: TimelineTrackKind, item: TimelineItem): TimelinePayload {
  const items = [...itemsOf(timeline, kind), item].sort((a, b) => Number(a.start) - Number(b.start))
  return withItems(timeline, kind, items)
}

export function removeItem(timeline: TimelinePayload, kind: TimelineTrackKind, idx: number): TimelinePayload {
  return withItems(
    timeline,
    kind,
    itemsOf(timeline, kind).filter((_, i) => i !== idx),
  )
}
