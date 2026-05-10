import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ThumbsUp, ThumbsDown, Play } from 'lucide-react'
import type { ClipRecord } from '@/api/client'
import { api } from '@/api/client'
import { scoreToGrade, formatTime, formatTimestamp } from '@/lib/scoreToGrade'

interface ClipListRowProps {
  clip: ClipRecord
  rank: number
  jobId: string
}

const SCORE_KEYS = ['hook', 'flow', 'value', 'trend'] as const

export function ClipListRow({ clip, rank, jobId }: ClipListRowProps) {
  const queryClient = useQueryClient()

  const likeMutation = useMutation({
    mutationFn: () => api.likeClip(clip.clip_id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['clips', jobId] }),
  })
  const dislikeMutation = useMutation({
    mutationFn: () => api.dislikeClip(clip.clip_id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['clips', jobId] }),
  })

  const startTs = formatTimestamp(clip.start ?? 0)

  const words: Array<{ word: string; start: number; end: number }> =
    clip.transcript && Array.isArray((clip.transcript as Record<string, unknown>).words)
      ? ((clip.transcript as Record<string, unknown>).words as Array<{ word: string; start: number; end: number }>)
      : []
  const transcriptText = words.map((w) => w.word).join(' ')

  return (
    <div className="flex items-start gap-6 py-6 border-b border-[#1f1f1f]">
      {/* Left col */}
      <div className="w-44 flex-shrink-0 space-y-2">
        <div className="flex items-baseline gap-1">
          <span className="text-2xl font-bold text-zinc-600">#{rank}</span>
        </div>
        <p className="text-xl font-semibold text-white line-clamp-2">{clip.title ?? 'Untitled clip'}</p>

        {/* Like / dislike */}
        <div className="flex gap-1">
          <button
            onClick={() => likeMutation.mutate()}
            className={`p-1.5 rounded-md transition-colors ${clip.liked ? 'text-white bg-white/10' : 'text-zinc-500 hover:text-white hover:bg-white/8'}`}
            title="Like"
          >
            <ThumbsUp className="w-4 h-4" />
          </button>
          <button
            onClick={() => dislikeMutation.mutate()}
            className={`p-1.5 rounded-md transition-colors ${clip.disliked ? 'text-white bg-white/10' : 'text-zinc-500 hover:text-white hover:bg-white/8'}`}
            title="Dislike"
          >
            <ThumbsDown className="w-4 h-4" />
          </button>
        </div>

        {/* Score */}
        <div className="flex items-baseline gap-1">
          <span className="text-4xl font-bold" style={{ color: 'var(--score-green)' }}>
            {clip.virality_score ?? '—'}
          </span>
          <span className="text-zinc-500 text-sm">/100</span>
        </div>

        {/* Letter grades */}
        <div className="space-y-1">
          {SCORE_KEYS.map((key) => (
            <div key={key} className="flex justify-between text-sm">
              <span className="text-zinc-400 capitalize">{key}</span>
              <span style={{ color: 'var(--score-green)' }}>
                {scoreToGrade(clip.score_breakdown?.[key])}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Center */}
      <div className="flex-1 min-w-0 flex gap-4">
        {/* Thumbnail */}
        <Link to="/clips/$clipId" params={{ clipId: clip.clip_id }} className="flex-shrink-0">
          <div className="aspect-[9/16] w-24 rounded-lg bg-zinc-900 overflow-hidden relative group">
            {clip.thumbnail_path ? (
              <img
                src={`/api/clips/${clip.clip_id}/thumbnail`}
                alt={clip.title ?? 'clip'}
                className="w-full h-full object-cover"
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center">
                <span className="text-zinc-600 text-[9px]">No preview</span>
              </div>
            )}
            <span className="absolute top-1 left-1 text-[8px] px-1 py-0.5 rounded bg-black/60 text-zinc-300">
              LOW-RES
            </span>
            <span className="absolute top-1 right-1 text-[8px] px-1 py-0.5 rounded bg-black/60 text-white">
              {startTs}
            </span>
            <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/30">
              <Play className="w-5 h-5 text-white" />
            </div>
          </div>
        </Link>

        {/* Scene analysis */}
        <div className="flex-1 min-w-0 space-y-2">
          <span className="text-xs text-zinc-400">[{formatTime(clip.start ?? 0)} – {formatTime(clip.end ?? 0)}]</span>
          {clip.summary && (
            <p className="text-sm text-zinc-300 line-clamp-2">{clip.summary}</p>
          )}
          {transcriptText && (
            <p className="text-xs text-zinc-500 line-clamp-3">{transcriptText}</p>
          )}
          <label className="flex items-center gap-1.5 text-xs text-zinc-500 cursor-pointer">
            <input type="checkbox" className="accent-white w-3 h-3" />
            Transcript only
          </label>
        </div>
      </div>

      {/* Right action col */}
      <ClipActionMenu clip={clip} jobId={jobId} />
    </div>
  )
}

function ClipActionMenu({ clip, jobId }: { clip: ClipRecord; jobId: string }) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState<string | null>(null)
  const [hook, setHook] = useState<string | null>(null)

  const aiHookMutation = useMutation({
    mutationFn: () => api.generateAiHook(clip.clip_id),
    onMutate: () => setBusy('ai-hook'),
    onSettled: () => setBusy(null),
    onSuccess: (resp) => setHook(resp.hook || '(no hook generated)'),
  })

  const enhanceMutation = useMutation({
    mutationFn: () => api.enhanceSpeech(clip.clip_id),
    onMutate: () => setBusy('enhance'),
    onSettled: () => {
      setBusy(null)
      void queryClient.invalidateQueries({ queryKey: ['clips', jobId] })
    },
  })

  const baseBtn =
    'w-full text-left text-xs px-2.5 py-1.5 rounded-md border border-white/20 text-zinc-200 hover:text-white hover:border-white/40 transition-colors disabled:opacity-50 disabled:cursor-wait'

  return (
    <div className="w-36 flex-shrink-0 flex flex-col gap-1.5">
      <Link
        to="/clips/$clipId/publish"
        params={{ clipId: clip.clip_id }}
        className={`${baseBtn} block`}
      >
        Publish on Social
      </Link>

      <a
        href={api.xmlExportUrl(clip.clip_id, 'premiere')}
        download
        className={baseBtn}
      >
        Export XML (Premiere)
      </a>
      <a
        href={api.xmlExportUrl(clip.clip_id, 'davinci')}
        download
        className={baseBtn}
      >
        Export FCPXML (Resolve)
      </a>

      <a
        href={`/api/clips/${clip.clip_id}/video`}
        download
        className={baseBtn}
      >
        Download HD
      </a>

      <Link
        to="/clips/$clipId/edit"
        params={{ clipId: clip.clip_id }}
        className={`${baseBtn} block`}
      >
        Edit clip
      </Link>

      <button
        type="button"
        disabled={busy === 'ai-hook'}
        onClick={() => aiHookMutation.mutate()}
        className={baseBtn}
      >
        {busy === 'ai-hook' ? 'Generating…' : 'AI hook'}
      </button>
      {hook ? (
        <p className="text-[10px] text-emerald-400 line-clamp-2 px-0.5" title={hook}>
          {hook}
        </p>
      ) : null}

      <button
        type="button"
        disabled={busy === 'enhance'}
        onClick={() => enhanceMutation.mutate()}
        className={baseBtn}
      >
        {busy === 'enhance' ? 'Enhancing…' : 'Enhance speech'}
      </button>
    </div>
  )
}
