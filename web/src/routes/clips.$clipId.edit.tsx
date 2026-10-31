import { createRoute, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useRef, useState, useEffect, useCallback, useMemo } from 'react'
import {
  ArrowLeft,
  Undo2,
  Redo2,
  Download,
  RefreshCw,
  Type,
  AlignLeft,
  Mic,
  Scissors,
  Play,
  Pause,
  type LucideIcon,
} from 'lucide-react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import type { VoiceoverRequest } from '@/api/client'
import { apiErrorDetail, apiErrorStatus } from '@/lib/apiErrorDetail'
import { toast } from 'sonner'
import { useTimelineEditor } from '@/hooks/useTimelineEditor'
import { MultiTrackTimeline } from '@/components/editor/MultiTrackTimeline'
import { BrollCredits } from '@/components/broll-credits'
import {
  CaptionsPanel,
  TextPanel,
  TrimPanel,
  VoiceoverPanel,
} from '@/components/editor/EditorToolPanels'
import { isV2, itemsOf, reelDuration, setCaptionText, type SourceWindow } from '@/lib/timelineEdit'

export const clipEditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/clips/$clipId/edit',
  component: ClipEditorPage,
})

type ToolId = 'captions' | 'text' | 'voiceover' | 'trim'

// Only tools the render applies (precedent T013: the editor does not
// advertise features that do nothing). Reframe is the server's
// YTVIDEO_REFRAME_PROVIDER, not a per-clip setting, so it has no tool here.
const RIGHT_TOOLS: Array<{ id: ToolId; label: string; icon: LucideIcon }> = [
  { id: 'captions', label: 'Captions', icon: AlignLeft },
  { id: 'text', label: 'Text', icon: Type },
  { id: 'voiceover', label: 'Voice-over', icon: Mic },
  { id: 'trim', label: 'Trim', icon: Scissors },
]

type Word = { word: string; start: number; end: number }

function formatVideoTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  const cs = Math.floor((seconds % 1) * 100)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${cs.toString().padStart(2, '0')}`
}

/** A voice-over request's failure as shown in its panel. */
function voiceoverErrorMessage(err: unknown): string {
  const status = apiErrorStatus(err)
  const detail = apiErrorDetail(err)
  const reason = detail ? `: ${detail}` : ''
  if (status === 409) return `Cannot add a voice-over (409)${reason}`
  if (status === 503) return `Voice-over is unavailable on the server (503)${reason}`
  if (status === 502) return `The voice engine failed (502)${reason}`
  if (status !== null) return `Voice-over failed (${status})${reason}`
  return `Voice-over failed: ${String(err)}`
}

function CaptionRow({
  index,
  start,
  end,
  text,
  active,
  onSeek,
  onCommit,
}: {
  index: number
  start: number
  end: number
  text: string
  active: boolean
  onSeek: () => void
  onCommit: (text: string) => void
}) {
  return (
    <li className={`flex items-start gap-2 rounded px-1 py-0.5 ${active ? 'bg-white/10' : ''}`}>
      <button
        type="button"
        onClick={onSeek}
        title={`Jump to ${formatVideoTime(start)}`}
        className="mt-1 font-mono text-[10px] text-zinc-500 hover:text-white"
      >
        {formatVideoTime(start)}
      </button>
      <input
        key={text}
        aria-label={`Caption ${index + 1}`}
        title={`${formatVideoTime(start)}–${formatVideoTime(end)}`}
        defaultValue={text}
        maxLength={500}
        onBlur={(e) => {
          if (e.target.value !== text) onCommit(e.target.value)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
        }}
        className="flex-1 bg-transparent text-sm text-zinc-200 border-b border-transparent hover:border-white/15 focus:border-white/40 focus:outline-none"
      />
    </li>
  )
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
  const [regenerateCopy, setRegenerateCopy] = useState(true)
  const [activeTool, setActiveTool] = useState<ToolId | null>(null)

  const clipsQuery = useQuery({
    queryKey: ['clips'],
    queryFn: () => api.listClips(),
  })
  const clip = (clipsQuery.data ?? []).find((c) => c.clip_id === clipId)

  const clipStart = clip?.start
  const clipEnd = clip?.end
  const sourceWindow = useMemo<SourceWindow | null>(
    () =>
      typeof clipStart === 'number' && typeof clipEnd === 'number' && clipEnd > clipStart && clipStart >= 0
        ? { start: clipStart, end: clipEnd }
        : null,
    [clipStart, clipEnd],
  )
  const editor = useTimelineEditor(clipId, { sourceWindow })

  const rerenderMutation = useMutation({
    mutationFn: () =>
      api.rerenderClip(clipId, { reframe_provider: 'letterbox', regenerate_copy: regenerateCopy }),
    onSuccess: () => toast.success('Re-render queued'),
    onError: (err) => {
      const detail = apiErrorDetail(err)
      toast.error(detail ? `Cannot re-render: ${detail}` : 'Failed to queue re-render')
    },
  })

  // The render reads the saved edit, so pending changes are saved first.
  const rerender = useCallback(async () => {
    if (editor.isDirty) {
      try {
        await editor.save()
      } catch (e) {
        toast.error(`Save failed: ${apiErrorDetail(e) ?? String(e)}`)
        return
      }
    }
    rerenderMutation.mutate()
  }, [editor, rerenderMutation])

  const voiceoverMutation = useMutation({
    mutationFn: async (body: VoiceoverRequest) => {
      // The server appends to the saved edit and refuses a saved v1 one:
      // save local changes (as v2) first.
      if (editor.isDirty || (editor.version > 0 && !isV2(editor.timeline))) await editor.save()
      return api.createVoiceover(clipId, body)
    },
    onSuccess: (res) => {
      editor.applyServerState?.(res.edit)
      toast.success('Voice-over added')
    },
  })

  const transcriptWords = clip?.transcript && Array.isArray((clip.transcript as Record<string, unknown>).words)
    ? ((clip.transcript as Record<string, unknown>).words as Word[])
    : null
  const editorWords = editor.words
  const words: Word[] = useMemo(
    () => (editorWords && editorWords.length > 0 ? editorWords : (transcriptWords ?? [])),
    [editorWords, transcriptWords],
  )
  const captions = itemsOf(editor.timeline, 'caption')

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

  const seek = useCallback((t: number) => {
    if (videoRef.current) videoRef.current.currentTime = t
  }, [])

  if (clipsQuery.isLoading) {
    return <div className="flex items-center justify-center h-full text-zinc-400">Loading…</div>
  }
  if (!clip) {
    return <div className="flex items-center justify-center h-full text-red-400">Clip not found.</div>
  }

  const busy = editor.isSaving || rerenderMutation.isPending

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
              (e) => toast.error(`Save failed: ${apiErrorDetail(e) ?? String(e)}`),
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

        <button
          type="button"
          disabled={busy}
          onClick={() => void rerender()}
          title="Save the edit and render the clip with it"
          className="px-3 py-1.5 rounded-lg border border-white/20 text-sm text-zinc-300 hover:text-white hover:border-white/40 transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center gap-1.5"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Re-render
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

          {captions.length > 0 ? (
            <ol aria-label="Captions" className="space-y-1">
              {captions.map((c, i) => {
                const start = Number(c.start) || 0
                const end = Number(c.end) || 0
                return (
                  <CaptionRow
                    key={i}
                    index={i}
                    start={start}
                    end={end}
                    text={String(c.text ?? '')}
                    active={currentTime >= start && currentTime < end}
                    onSeek={() => seek(start)}
                    onCommit={(text) => editor.setTimeline(setCaptionText(editor.timeline, i, text))}
                  />
                )
              })}
            </ol>
          ) : words.length > 0 ? (
            <div className="text-sm leading-relaxed">
              {words.map((w, i) => (
                <span
                  key={i}
                  onClick={() => seek(w.start)}
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

          <label className="flex items-center gap-2 text-xs text-zinc-300 cursor-pointer">
            <input
              type="checkbox"
              checked={regenerateCopy}
              onChange={(e) => setRegenerateCopy(e.target.checked)}
              className="accent-white"
            />
            Regenerate title, summary and hashtags
          </label>

          <button
            onClick={() => void rerender()}
            disabled={busy}
            className="text-xs text-zinc-400 border border-white/10 px-3 py-1.5 rounded-md hover:text-white hover:border-white/30 transition-colors disabled:opacity-60"
          >
            {rerenderMutation.isPending ? 'Queuing…' : '+ Regenerate captions'}
          </button>

          <BrollCredits assets={clip.broll_assets} className="pt-4 border-t border-border" />
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
              <span className="absolute top-3 left-3 text-[10px] px-2 py-0.5 rounded bg-black/70 text-white border border-white/20">
                9:16
              </span>
            </>
          ) : (
            <div className="aspect-[9/16] max-h-full bg-zinc-900 flex items-center justify-center rounded-lg">
              <p className="text-zinc-500 text-sm">Video not available</p>
            </div>
          )}
        </div>

        {/* Active tool panel */}
        {activeTool ? (
          <div
            key={activeTool}
            className="w-72 flex-shrink-0 overflow-y-auto border-l border-white/8 p-4"
            style={{ background: 'var(--sidebar-bg)' }}
          >
            {activeTool === 'captions' ? (
              <CaptionsPanel timeline={editor.timeline} onChange={editor.setTimeline} />
            ) : activeTool === 'text' ? (
              <TextPanel timeline={editor.timeline} onChange={editor.setTimeline} playhead={currentTime} />
            ) : activeTool === 'voiceover' ? (
              <VoiceoverPanel
                timeline={editor.timeline}
                onChange={editor.setTimeline}
                playhead={currentTime}
                defaultText={clip.ai_hook_text ?? ''}
                pending={voiceoverMutation.isPending}
                error={voiceoverMutation.error ? voiceoverErrorMessage(voiceoverMutation.error) : null}
                onGenerate={(req) => voiceoverMutation.mutate(req)}
              />
            ) : (
              <TrimPanel
                timeline={editor.timeline}
                onChange={editor.setTimeline}
                playhead={currentTime}
                sourceWindow={sourceWindow}
              />
            )}
          </div>
        ) : null}

        {/* Right tools sidebar */}
        <div
          className="w-16 flex-shrink-0 flex flex-col items-center gap-3 py-4 border-l border-white/8"
          style={{ background: 'var(--sidebar-bg)' }}
          role="toolbar"
          aria-label="Editor tools"
          aria-orientation="vertical"
        >
          {RIGHT_TOOLS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              title={label}
              aria-label={label}
              aria-pressed={activeTool === id}
              onClick={() => setActiveTool((cur) => (cur === id ? null : id))}
              className={`p-2 rounded-lg transition-colors ${
                activeTool === id ? 'text-white bg-white/15' : 'text-zinc-400 hover:text-white hover:bg-white/10'
              }`}
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
            onChange={(e) => seek(Number(e.target.value))}
            className="flex-1 h-1 accent-white cursor-pointer"
          />
        </div>

        <MultiTrackTimeline
          timeline={editor.timeline}
          duration={Math.max(duration, reelDuration(editor.timeline), 1)}
          onChange={editor.setTimeline}
        />
      </div>
    </div>
  )
}
