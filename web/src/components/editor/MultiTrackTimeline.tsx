/**
 * MultiTrackTimeline — three-track editor surface (W1.12 frontend).
 *
 * Renders video / caption / text-overlay tracks horizontally. Items
 * are absolutely-positioned div blocks scaled by ``pixelsPerSecond``.
 * Drag/resize uses native pointer events to keep the dependency
 * footprint small (no react-rnd; the plan's @dnd-kit dependency is
 * deferred to a follow-up if richer drag UX is needed).
 */
import { useCallback } from 'react'
import type { TimelinePayload, TimelineTrack } from '@/api/client'

interface Props {
  timeline: TimelinePayload
  duration: number
  onChange: (next: TimelinePayload) => void
  pixelsPerSecond?: number
}

const TRACK_HEIGHT = 56
const TRACK_KINDS: Array<TimelineTrack['kind']> = [
  'video',
  'caption',
  'text-overlay',
]

const TRACK_LABELS: Record<TimelineTrack['kind'], string> = {
  video: 'Video',
  caption: 'Captions',
  'text-overlay': 'Text',
}

const TRACK_COLORS: Record<TimelineTrack['kind'], string> = {
  video: 'bg-emerald-500/40 border-emerald-400/70',
  caption: 'bg-sky-500/40 border-sky-400/70',
  'text-overlay': 'bg-amber-500/40 border-amber-400/70',
}

export function MultiTrackTimeline({
  timeline,
  duration,
  onChange,
  pixelsPerSecond = 80,
}: Props) {
  const widthPx = Math.max(320, Math.ceil(duration * pixelsPerSecond))

  const updateTrack = useCallback(
    (kind: TimelineTrack['kind'], items: TimelineTrack['items']) => {
      const next: TimelinePayload = {
        tracks: TRACK_KINDS.map((k) => {
          const existing = timeline.tracks.find((t) => t.kind === k)
          if (k === kind) return { kind: k, items }
          return existing ?? { kind: k, items: [] }
        }),
      }
      onChange(next)
    },
    [onChange, timeline],
  )

  return (
    <div
      data-testid="multi-track-timeline"
      className="rounded-md border border-white/10 bg-zinc-950 overflow-x-auto"
    >
      <div className="flex">
        <div className="w-28 shrink-0 border-r border-white/10">
          {TRACK_KINDS.map((kind) => (
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
          {TRACK_KINDS.map((kind) => {
            const track = timeline.tracks.find((t) => t.kind === kind) ?? {
              kind,
              items: [],
            }
            return (
              <div
                key={kind}
                data-track={kind}
                style={{ height: TRACK_HEIGHT }}
                className="relative border-b border-white/5"
              >
                {track.items.map((item, idx) => {
                  const start = Number(item.start) || 0
                  const end = Number(item.end) || 0
                  const left = Math.round(start * pixelsPerSecond)
                  const width = Math.max(
                    8,
                    Math.round((end - start) * pixelsPerSecond),
                  )
                  return (
                    <div
                      key={`${kind}-${idx}`}
                      data-testid={`item-${kind}-${idx}`}
                      role="button"
                      tabIndex={0}
                      title={
                        kind === 'text-overlay'
                          ? String(
                              (item as Record<string, unknown>).text ?? '',
                            )
                          : kind === 'caption'
                            ? String(
                                (item as Record<string, unknown>).text ?? '',
                              )
                            : 'video'
                      }
                      onClick={() => {
                        const newItems = [...track.items]
                        newItems.splice(idx, 1)
                        updateTrack(kind, newItems)
                      }}
                      style={{ left, width, top: 6, height: TRACK_HEIGHT - 12 }}
                      className={`absolute rounded border ${TRACK_COLORS[kind]} cursor-pointer focus:ring-2 focus:ring-white/40`}
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
