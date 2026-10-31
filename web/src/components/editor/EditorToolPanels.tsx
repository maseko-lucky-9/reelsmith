/**
 * The clip editor's tool panels (spec 016): captions style, text overlays,
 * voice-over and trim. Each is presentational: it reads the timeline and
 * hands a new one (or a request) to the editor page.
 */
import { useState } from 'react'
import type { TimelineItem, TimelinePayload } from '@/api/client'
import {
  CAPTION_TEMPLATES,
  CaptionTemplatePicker,
  type CaptionStyleName,
} from '@/components/editor/CaptionTemplatePicker'
import {
  MIN_SEGMENT_SECONDS,
  addItem,
  isV2,
  itemsOf,
  reelDuration,
  removeItem,
  segmentsOf,
  setCaptionStyle,
  setSegments,
  splitAt,
  upgradeTimeline,
  type Segment,
  type SourceWindow,
} from '@/lib/timelineEdit'

const fmt = (n: number) => (Math.round(n * 100) / 100).toFixed(2)

const inputClass =
  'w-full rounded-md border border-white/15 bg-black/30 px-2 py-1 text-xs text-zinc-100 focus:border-white/40 focus:outline-none'
const buttonClass =
  'text-xs text-zinc-300 border border-white/15 px-2 py-1 rounded-md hover:text-white hover:border-white/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed'

interface TimelineProps {
  timeline: TimelinePayload
  onChange: (next: TimelinePayload) => void
}

function PanelTitle({ children }: { children: string }) {
  return <h2 className="text-sm font-semibold text-zinc-100">{children}</h2>
}

// ── Captions ────────────────────────────────────────────────────────────────

export function CaptionsPanel({ timeline, onChange }: TimelineProps) {
  // A stored key outside the presets shows as no selection.
  const style = CAPTION_TEMPLATES.some((t) => t.name === timeline.caption_style)
    ? (timeline.caption_style as CaptionStyleName)
    : null
  return (
    <section aria-label="Captions" className="space-y-3">
      <PanelTitle>Caption style</PanelTitle>
      <p className="text-[11px] text-zinc-500">
        Applied when the clip is re-rendered. Edit the caption words in the transcript panel.
      </p>
      <CaptionTemplatePicker value={style} onChange={(name) => onChange(setCaptionStyle(timeline, name))} />
      <button
        type="button"
        className={buttonClass}
        disabled={style === null}
        onClick={() => onChange(setCaptionStyle(timeline, null))}
      >
        Use the default style
      </button>
    </section>
  )
}

// ── Text overlays ───────────────────────────────────────────────────────────

interface TextPanelProps extends TimelineProps {
  playhead: number
}

const OVERLAY_SECONDS = 3

export function TextPanel({ timeline, onChange, playhead }: TextPanelProps) {
  const [text, setText] = useState('')
  const [start, setStart] = useState(() => Math.round(playhead * 100) / 100)
  const [seconds, setSeconds] = useState(OVERLAY_SECONDS)
  const overlays = itemsOf(timeline, 'text-overlay')
  const reelEnd = reelDuration(timeline)

  const end = reelEnd > 0 ? Math.min(start + seconds, reelEnd) : start + seconds
  const valid = text.trim().length > 0 && start >= 0 && end - start >= MIN_SEGMENT_SECONDS

  return (
    <section aria-label="Text" className="space-y-3">
      <PanelTitle>Text overlay</PanelTitle>
      <label className="block text-xs text-zinc-400">
        Text
        <input className={inputClass} value={text} maxLength={500} onChange={(e) => setText(e.target.value)} />
      </label>
      <div className="flex gap-2">
        <label className="block text-xs text-zinc-400">
          Start (s)
          <input
            className={inputClass}
            type="number"
            min={0}
            step={0.1}
            value={start}
            onChange={(e) => setStart(Number(e.target.value))}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Length (s)
          <input
            className={inputClass}
            type="number"
            min={MIN_SEGMENT_SECONDS}
            step={0.5}
            value={seconds}
            onChange={(e) => setSeconds(Number(e.target.value))}
          />
        </label>
      </div>
      <button
        type="button"
        className={buttonClass}
        disabled={!valid}
        onClick={() => {
          onChange(
            addItem(timeline, 'text-overlay', {
              start: Math.round(start * 1000) / 1000,
              end: Math.round(end * 1000) / 1000,
              text: text.trim(),
              x: 0.5,
              y: 0.1,
            }),
          )
          setText('')
        }}
      >
        Add text
      </button>
      <ItemList
        label="Text overlays"
        items={overlays}
        describe={(it) => String(it.text ?? '')}
        onRemove={(idx) => onChange(removeItem(timeline, 'text-overlay', idx))}
      />
    </section>
  )
}

function ItemList({
  label,
  items,
  describe,
  onRemove,
}: {
  label: string
  items: TimelineItem[]
  describe: (item: TimelineItem) => string
  onRemove: (idx: number) => void
}) {
  if (items.length === 0) return null
  return (
    <ul aria-label={label} className="space-y-1">
      {items.map((it, idx) => (
        <li key={idx} className="flex items-center gap-2 text-xs text-zinc-300">
          <span className="font-mono text-zinc-500">
            {fmt(Number(it.start))}–{fmt(Number(it.end))}
          </span>
          <span className="flex-1 truncate">{describe(it)}</span>
          <button
            type="button"
            className="text-zinc-500 hover:text-red-400"
            aria-label={`Remove ${describe(it)}`}
            onClick={() => onRemove(idx)}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  )
}

// ── Voice-over ──────────────────────────────────────────────────────────────

interface VoiceoverPanelProps extends TimelineProps {
  playhead: number
  /** The clip's AI hook: the default script. */
  defaultText: string
  pending: boolean
  error: string | null
  onGenerate: (request: { text: string; start: number }) => void
}

export function VoiceoverPanel({
  timeline,
  onChange,
  playhead,
  defaultText,
  pending,
  error,
  onGenerate,
}: VoiceoverPanelProps) {
  const [text, setText] = useState(defaultText)
  const [start, setStart] = useState(() => Math.round(playhead * 100) / 100)
  const audio = itemsOf(timeline, 'audio')
  return (
    <section aria-label="Voice-over" className="space-y-3">
      <PanelTitle>Voice-over</PanelTitle>
      <p className="text-[11px] text-zinc-500">
        Speaks the script over the clip; the source audio is ducked under it. Unsaved edits are saved first.
      </p>
      <label className="block text-xs text-zinc-400">
        Script
        <textarea
          className={`${inputClass} min-h-24`}
          value={text}
          maxLength={2000}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <label className="block text-xs text-zinc-400">
        Start (s)
        <input
          className={inputClass}
          type="number"
          min={0}
          step={0.1}
          value={start}
          onChange={(e) => setStart(Number(e.target.value))}
        />
      </label>
      <button
        type="button"
        className={buttonClass}
        disabled={pending || text.trim().length === 0 || !(start >= 0)}
        onClick={() => onGenerate({ text: text.trim(), start })}
      >
        {pending ? 'Generating…' : 'Generate voice-over'}
      </button>
      {error ? (
        <p role="alert" className="text-xs text-red-400">
          {error}
        </p>
      ) : null}
      <ItemList
        label="Voice-overs"
        items={audio}
        describe={(it) => String(it.src ?? 'voice-over')}
        onRemove={(idx) => onChange(removeItem(timeline, 'audio', idx))}
      />
    </section>
  )
}

// ── Trim ────────────────────────────────────────────────────────────────────

interface TrimPanelProps extends TimelineProps {
  playhead: number
  sourceWindow: SourceWindow | null
}

export function TrimPanel({ timeline, onChange, playhead, sourceWindow }: TrimPanelProps) {
  const [error, setError] = useState<string | null>(null)
  const v2 = upgradeTimeline(timeline, sourceWindow)
  if (!v2) {
    return (
      <section aria-label="Trim" className="space-y-3">
        <PanelTitle>Trim</PanelTitle>
        <p className="text-xs text-zinc-500">
          This clip&apos;s place in its source video is unknown, so it cannot be trimmed here.
        </p>
      </section>
    )
  }
  const segments = segmentsOf(v2)
  const split = splitAt(v2, playhead)

  const commit = (idx: number, field: keyof Segment, raw: string) => {
    const value = Number(raw)
    const next = segments.map((s, i) => (i === idx ? { ...s, [field]: value } : s))
    const seg = next[idx]
    if (!Number.isFinite(value) || value < 0 || seg.end - seg.start < MIN_SEGMENT_SECONDS) {
      setError(`Segment ${idx + 1}: the end must be at least ${MIN_SEGMENT_SECONDS}s after the start.`)
      return
    }
    if ((idx > 0 && seg.start < next[idx - 1].end) || (idx < next.length - 1 && seg.end > next[idx + 1].start)) {
      setError(`Segment ${idx + 1}: segments must stay in order without overlapping.`)
      return
    }
    setError(null)
    if (value !== segments[idx][field]) onChange(setSegments(v2, next))
  }

  return (
    <section aria-label="Trim" className="space-y-3">
      <PanelTitle>Trim</PanelTitle>
      <p className="text-[11px] text-zinc-500">
        Times are seconds in the source video. Edited reel: {fmt(reelDuration(v2))}s
        {isV2(timeline) ? '' : ' (saved as a schema v2 edit)'}.
      </p>
      <ol className="space-y-2">
        {segments.map((s, idx) => (
          <li key={`${idx}-${s.start}-${s.end}`} className="flex items-end gap-2">
            <label className="block text-xs text-zinc-400">
              Segment {idx + 1} start
              <input
                className={inputClass}
                type="number"
                step={0.1}
                min={0}
                defaultValue={s.start}
                onBlur={(e) => commit(idx, 'start', e.target.value)}
              />
            </label>
            <label className="block text-xs text-zinc-400">
              Segment {idx + 1} end
              <input
                className={inputClass}
                type="number"
                step={0.1}
                min={0}
                defaultValue={s.end}
                onBlur={(e) => commit(idx, 'end', e.target.value)}
              />
            </label>
            <button
              type="button"
              className={buttonClass}
              aria-label={`Delete segment ${idx + 1}`}
              disabled={segments.length < 2}
              onClick={() => onChange(setSegments(v2, segments.filter((_, i) => i !== idx)))}
            >
              ×
            </button>
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={buttonClass} disabled={!split} onClick={() => split && onChange(split)}>
          Split at playhead
        </button>
        {sourceWindow ? (
          <button
            type="button"
            className={buttonClass}
            onClick={() => onChange(setSegments(v2, [{ start: sourceWindow.start, end: sourceWindow.end }]))}
          >
            Reset to the clip
          </button>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="text-xs text-red-400">
          {error}
        </p>
      ) : null}
    </section>
  )
}
