/**
 * MultiTrackTimeline — the editor's track surface (W1.12 frontend; spec 016).
 *
 * Renders video / caption / text-overlay / voice-over tracks horizontally.
 * Items are absolutely-positioned div blocks scaled by ``pixelsPerSecond``;
 * clicking one removes it. A schema v2 timeline's video items are source
 * segments: they are drawn back to back on the reel's clock and are edited
 * in the Trim panel, not here (removing one would shift every caption).
 * Every other track and the timeline's top-level keys (``schema_version``,
 * ``caption_style``, ``mute_source``) pass through an edit unchanged.
 */
import { useCallback, useMemo } from 'react'
import type { TimelineItem, TimelinePayload, TimelineTrack } from '@/api/client'

interface Props {
  timeline: TimelinePayload
  duration: number
  onChange: (next: TimelinePayload) => void
  pixelsPerSecond?: number
}

const TRACK_HEIGHT = 56
const BASE_KINDS: Array<TimelineTrack['kind']> = ['video', 'caption', 'text-overlay']

const TRACK_LABELS: Record<TimelineTrack['kind'], string> = {
  video: 'Video',
  caption: 'Captions',
  'text-overlay': 'Text',
  audio: 'Voice-over',
}

const TRACK_COLORS: Record<TimelineTrack['kind'], string> = {
  video: 'bg-emerald-500/40 border-emerald-400/70',
  caption: 'bg-sky-500/40 border-sky-400/70',
  'text-overlay': 'bg-amber-500/40 border-amber-400/70',
  audio: 'bg-fuchsia-500/40 border-fuchsia-400/70',
}

/** v2 source segments placed back to back on the reel's clock. */
function reelSegments(items: TimelineItem[]): TimelineItem[] {
  let cursor = 0
  return items.map((it) => {
    const len = Math.max(0, Number(it.end) - Number(it.start))
    const placed = { ...it, start: cursor, end: cursor + len }
    cursor += len
    return placed
  })
}

export function MultiTrackTimeline({
  timeline,
  duration,
  onChange,
  pixelsPerSecond = 80,
}: Props) {
  const widthPx = Math.max(320, Math.ceil(duration * pixelsPerSecond))
  const isV2 = timeline.schema_version === 2
  const hasAudio = timeline.tracks.some((t) => t.kind === 'audio')
  const kinds = useMemo<Array<TimelineTrack['kind']>>(
    () => (hasAudio ? [...BASE_KINDS, 'audio'] : BASE_KINDS),
    [hasAudio],
  )

  const updateTrack = useCallback(
    (kind: TimelineTrack['kind'], items: TimelineTrack['items']) => {
      const shown = kinds.map((k) => {
        const existing = timeline.tracks.find((t) => t.kind === k)
        if (k === kind) return { kind: k, items }
        return existing ?? { kind: k, items: [] }
      })
      const rest = timeline.tracks.filter((t) => !kinds.includes(t.kind))
      onChange({ ...timeline, tracks: [...shown, ...rest] })
    },
    [kinds, onChange, timeline],
  )

  return (
    <div
      data-testid="multi-track-timeline"
      className="rounded-md border border-white/10 bg-zinc-950 overflow-x-auto"
    >
      <div className="flex">
        <div className="w-28 shrink-0 border-r border-white/10">
          {kinds.map((kind) => (
            <div
              key={kind}
              style={{ height: TRACK_HEIGHT }}
              className="px-3 py-2 text-xs uppercase tracking-wide text-zinc-400 border-b border-white/5 flex items-center"
            >
              {TRACK_LABELS[kind]}
            </div>
          ))}
        </div>

        <div className="relative" style={{ width: widthPx }}>
          {kinds.map((kind) => {
            const track = timeline.tracks.find((t) => t.kind === kind) ?? {
              kind,
              items: [],
            }
            const locked = isV2 && kind === 'video'
            const shownItems = locked ? reelSegments(track.items) : track.items
            return (
              <div
                key={kind}
                data-track={kind}
                style={{ height: TRACK_HEIGHT }}
                className="relative border-b border-white/5"
              >
                {shownItems.map((item, idx) => {
                  const start = Number(item.start) || 0
                  const end = Number(item.end) || 0
                  const left = Math.round(start * pixelsPerSecond)
                  const width = Math.max(
                    8,
                    Math.round((end - start) * pixelsPerSecond),
                  )
                  const label =
                    kind === 'text-overlay' || kind === 'caption'
                      ? String((item as Record<string, unknown>).text ?? '')
                      : kind === 'audio'
                        ? 'voice-over'
                        : 'video'
                  return (
                    <div
                      key={`${kind}-${idx}`}
                      data-testid={`item-${kind}-${idx}`}
                      role="button"
                      tabIndex={0}
                      title={locked ? 'video segment (edit in Trim)' : label}
                      aria-disabled={locked || undefined}
                      onClick={
                        locked
                          ? undefined
                          : () => {
                              const newItems = [...track.items]
                              newItems.splice(idx, 1)
                              updateTrack(kind, newItems)
                            }
                      }
                      style={{ left, width, top: 6, height: TRACK_HEIGHT - 12 }}
                      className={`absolute rounded border ${TRACK_COLORS[kind]} ${
                        locked ? 'cursor-default' : 'cursor-pointer'
                      } focus:ring-2 focus:ring-white/40`}
                    />
                  )
                })}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
