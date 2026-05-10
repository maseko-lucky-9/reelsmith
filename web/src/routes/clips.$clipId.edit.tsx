import { createRoute, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useRef, useState, useEffect, useCallback } from 'react'
import {
  ArrowLeft,
  Undo2,
  Redo2,
  Download,
  Sparkles,
  Type,
  Layers,
  LayoutTemplate,
  Film,
  Wand2,
  AlignLeft,
  Music,
  Zap,
  Play,
  Pause,
} from 'lucide-react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import { toast } from 'sonner'
import { useTimelineEditor } from '@/hooks/useTimelineEditor'
import { MultiTrackTimeline } from '@/components/editor/MultiTrackTimeline'

export const clipEditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/clips/$clipId/edit',
  component: ClipEditorPage,
})

const RIGHT_TOOLS = [
  { label: 'AI enhance', icon: Sparkles },
  { label: 'Captions', icon: AlignLeft },
  { label: 'Media', icon: Layers },
  { label: 'Brand template', icon: LayoutTemplate },
  { label: 'B-Roll', icon: Film },
  { label: 'Transitions', icon: Wand2 },
  { label: 'Text', icon: Type },
  { label: 'Music', icon: Music },
  { label: 'AI hook', icon: Zap },
]

function formatVideoTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const cs = Math.floor((seconds % 1) * 100)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${cs.toString().padStart(2, '0')}`
}

export function ClipEditorPage() {
  const { clipId } = clipEditRoute.useParams()
  const navigate = useNavigate()
  const videoRef = useRef<HTMLVideoElement>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)

  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [activeWordIdx, setActiveWordIdx] = useState<number | null>(null)
  const [transcriptOnly, setTranscriptOnly] = useState(false)
  const editor = useTimelineEditor(clipId)

  const clipsQuery = useQuery({
    queryKey: ['clips'],
    queryFn: () => api.listClips(),
  })
  const clip = (clipsQuery.data ?? []).find((c) => c.clip_id === clipId)

  const rerenderMutation = useMutation({
    mutationFn: () => api.rerenderClip(clipId, { reframe_provider: 'letterbox' }),
    onSuccess: () => toast.success('Re-render queued'),
    onError: () => toast.error('Failed to queue re-render'),
  })

  const words: Array<{ word: string; start: number; end: number }> =
    clip?.transcript && Array.isArray((clip.transcript as Record<string, unknown>).words)
      ? ((clip.transcript as Record<string, unknown>).words as Array<{ word: string; start: number; end: number }>)
      : []

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const onTimeUpdate = () => {
      setCurrentTime(video.currentTime)
      if (words.length > 0) {
        const idx = words.findIndex((w) => video.currentTime >= w.start && video.currentTime < w.end)
        setActiveWordIdx(idx >= 0 ? idx : null)
      }
    }
    const onDurationChange = () => setDuration(video.duration)
    const onPlay = () => setIsPlaying(true)
    const onPause = () => setIsPlaying(false)

    video.addEventListener('timeupdate', onTimeUpdate)
    video.addEventListener('durationchange', onDurationChange)
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate)
      video.removeEventListener('durationchange', onDurationChange)
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
    }
  }, [words])

  const togglePlay = useCallback(() => {
    const v = videoRef.current
    if (!v) return
    if (v.paused) void v.play()
    else v.pause()
  }, [])

  if (clipsQuery.isLoading) {
    return <div className="flex items-center justify-center h-full text-zinc-400">Loading…</div>
  }
  if (!clip) {
    return <div className="flex items-center justify-center h-full text-red-400">Clip not found.</div>
  }

  return (
    <div className="flex flex-col h-full -mx-6 -my-6 overflow-hidden" style={{ height: 'calc(100vh - 48px - 32px)' }}>
      {/* Editor top bar */}
      <div
        className="flex items-center gap-3 px-4 py-2 border-b border-white/8 flex-shrink-0"
        style={{ background: 'var(--sidebar-bg)' }}
      >
        <button
          onClick={() => void navigate({ to: '/jobs/$jobId', params: { jobId: clip.job_id } })}
          className="p-1.5 rounded-md text-zinc-400 hover:text-white hover:bg-white/10 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>

        <span className="text-sm text-white font-medium truncate max-w-[200px]">
          {clip.title ?? 'Untitled clip'}
        </span>

        <div className="flex gap-1 ml-2">
          <button
            type="button"
            disabled={!editor.canUndo}
            onClick={editor.undo}
            className="p-1.5 rounded-md text-zinc-300 hover:text-white disabled:text-zinc-600 disabled:cursor-not-allowed"
            title="Undo"
          >
            <Undo2 className="w-4 h-4" />
          </button>
          <button
            type="button"
            disabled={!editor.canRedo}
            onClick={editor.redo}
            className="p-1.5 rounded-md text-zinc-300 hover:text-white disabled:text-zinc-600 disabled:cursor-not-allowed"
            title="Redo"
          >
            <Redo2 className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1" />

        <button
          type="button"
          disabled={!editor.isDirty || editor.isSaving}
          onClick={() =>
            editor.save().then(
              () => toast.success('Saved'),
              (e) => toast.error(`Save failed: ${e}`),
            )
          }
          className="px-3 py-1.5 rounded-lg border border-white/20 text-sm text-zinc-300 hover:text-white hover:border-white/40 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {editor.isSaving
            ? 'Saving…'
            : editor.isDirty
              ? 'Save changes'
              : 'Saved'}
        </button>

        <a
          href={`/api/clips/${clip.clip_id}/video`}
          download
          className="px-3 py-1.5 rounded-lg bg-white text-black text-sm font-semibold hover:bg-zinc-100 transition-colors flex items-center gap-1.5"
        >
          <Download className="w-3.5 h-3.5" />
          Export
        </a>
      </div>

      {/* Main editor area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left transcript panel */}
        <div
          ref={transcriptRef}
          className="w-80 flex-shrink-0 overflow-y-auto border-r border-white/8 p-4 space-y-4"
          style={{ background: 'var(--sidebar-bg)' }}
        >
          <label className="flex items-center gap-2 text-sm text-zinc-300 cursor-pointer">
            <input
              type="checkbox"
              checked={transcriptOnly}
              onChange={(e) => setTranscriptOnly(e.target.checked)}
              className="accent-white"
            />
            Transcript only
          </label>

          <button
            disabled
            className="text-xs text-zinc-500 border border-white/10 px-2 py-1 rounded-md cursor-not-allowed opacity-50"
          >
            + Add a section
          </button>

          {words.length > 0 ? (
            <div className="text-sm leading-relaxed">
              {words.map((w, i) => (
                <span
                  key={i}
                  onClick={() => {
                    if (videoRef.current) videoRef.current.currentTime = w.start
                  }}
                  className={`cursor-pointer rounded px-0.5 transition-colors ${
                    activeWordIdx === i
                      ? 'text-black font-medium'
                      : 'text-zinc-300 hover:text-white'
                  }`}
                  style={activeWordIdx === i ? { backgroundColor: 'var(--score-green)' } : undefined}
                >
                  {w.word}{' '}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-zinc-500 text-xs">No transcript available.</p>
          )}

          <button
            onClick={() => rerenderMutation.mutate()}
            disabled={rerenderMutation.isPending}
            className="text-xs text-zinc-400 border border-white/10 px-3 py-1.5 rounded-md hover:text-white hover:border-white/30 transition-colors disabled:opacity-60"
          >
            {rerenderMutation.isPending ? 'Queuing…' : '+ Regenerate captions'}
          </button>
        </div>

        {/* Center video */}
        <div className="flex-1 overflow-hidden flex items-center justify-center bg-black relative">
          {clip.output_path ? (
            <>
              <video
                ref={videoRef}
                src={`/api/clips/${clip.clip_id}/video`}
                className="max-h-full"
                style={{ aspectRatio: '9/16' }}
              />
              {/* Overlay chips */}
              <span className="absolute top-3 left-3 text-[10px] px-2 py-0.5 rounded bg-black/70 text-white border border-white/20">
                9:16
              </span>
              <span className="absolute top-3 right-3 flex gap-1.5">
                <span className="text-[10px] px-2 py-0.5 rounded bg-black/70 text-white border border-white/20">
                  Layout: Fit
                </span>
                <span className="text-[10px] px-2 py-0.5 rounded bg-black/70 text-white border border-white/20">
                  Tracker: OFF
                </span>
              </span>
              {/* Corner handles */}
              {[
                'top-2 left-2', 'top-2 right-2', 'bottom-2 left-2', 'bottom-2 right-2',
              ].map((pos) => (
                <span
                  key={pos}
                  className={`absolute ${pos} w-3 h-3 border-2 border-white rounded-sm bg-transparent pointer-events-none`}
                />
              ))}
            </>
          ) : (
            <div className="aspect-[9/16] max-h-full bg-zinc-900 flex items-center justify-center rounded-lg">
              <p className="text-zinc-500 text-sm">Video not available</p>
            </div>
          )}
        </div>

        {/* Right tools sidebar */}
        <div
          className="w-16 flex-shrink-0 flex flex-col items-center gap-3 py-4 border-l border-white/8"
          style={{ background: 'var(--sidebar-bg)' }}
        >
          {RIGHT_TOOLS.map(({ label, icon: Icon }) => (
            <button
              key={label}
              title={label}
              onClick={label === 'Brand template' ? () => void navigate({ to: '/settings/brand' }) : undefined}
              className="p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-white/10 transition-colors"
            >
              <Icon className="w-5 h-5" />
            </button>
          ))}
        </div>
      </div>

      {/* Bottom timeline */}
      <div
        className="px-4 pt-3 pb-4 border-t border-white/8 flex-shrink-0 flex flex-col gap-3"
        style={{ background: 'var(--sidebar-bg)' }}
      >
        <div className="flex items-center gap-4">
          <button
            onClick={togglePlay}
            className="p-1.5 rounded-md text-zinc-300 hover:text-white hover:bg-white/10 transition-colors"
          >
            {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
          </button>

          <span className="text-xs text-zinc-400 font-mono whitespace-nowrap">
            {formatVideoTime(currentTime)} / {formatVideoTime(duration)}
          </span>

          <input
            type="range"
            min={0}
            max={duration || 1}
            step={0.01}
            value={currentTime}
            onChange={(e) => {
              const v = videoRef.current
              if (v) v.currentTime = Number(e.target.value)
            }}
            className="flex-1 h-1 accent-white cursor-pointer"
          />
        </div>

        <MultiTrackTimeline
          timeline={editor.timeline}
          duration={Math.max(duration, 1)}
          onChange={editor.setTimeline}
        />
      </div>
    </div>
  )
}
