import { createRoute, Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useRef, useState, useEffect } from 'react'
import { rootRoute } from './root'
import { api, type ClipRecord } from '@/api/client'
import { ScoreBadge } from '@/components/dashboard/ScoreBadge'
import { scoreToGrade, formatTime } from '@/lib/scoreToGrade'

export const clipDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/clips/$clipId',
  component: ClipDetailPage,
})

const SCORE_KEYS = ['hook', 'flow', 'value', 'trend'] as const

function ClipDetailPage() {
  const { clipId } = clipDetailRoute.useParams()

  const clipsQuery = useQuery({
    queryKey: ['clips'],
    queryFn: () => api.listClips(),
  })

  const clip = (clipsQuery.data ?? []).find((c) => c.clip_id === clipId)

  if (clipsQuery.isLoading) return <p className="text-zinc-400">Loading…</p>
  if (!clip) return <p className="text-red-400">Clip not found.</p>

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-8 max-w-5xl">
      <VideoPanel clip={clip} />
      <InfoPanel clip={clip} />
    </div>
  )
}

function VideoPanel({ clip }: { clip: ClipRecord }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [activeWordIdx, setActiveWordIdx] = useState<number | null>(null)

  const words: Array<{ word: string; start: number; end: number }> =
    clip.transcript && Array.isArray((clip.transcript as Record<string, unknown>).words)
      ? ((clip.transcript as Record<string, unknown>).words as Array<{ word: string; start: number; end: number }>)
      : []

  useEffect(() => {
    const video = videoRef.current
    if (!video || words.length === 0) return
    const onTimeUpdate = () => {
      const t = video.currentTime
      const idx = words.findIndex((w) => t >= w.start && t < w.end)
      setActiveWordIdx(idx >= 0 ? idx : null)
    }
    video.addEventListener('timeupdate', onTimeUpdate)
    return () => video.removeEventListener('timeupdate', onTimeUpdate)
  }, [words])

  const duration = ((clip.end ?? 0) - (clip.start ?? 0)).toFixed(0)

  return (
    <div className="space-y-4">
      {clip.output_path ? (
        <video
          ref={videoRef}
          src={`/api/clips/${clip.clip_id}/video`}
          controls
          className="w-full max-h-[70vh] object-contain bg-black rounded-lg"
        />
      ) : (
        <div className="aspect-video bg-zinc-900 rounded-lg flex items-center justify-center">
          <p className="text-zinc-500 text-sm">Video not available</p>
        </div>
      )}

      {/* Timestamp + actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs px-2.5 py-1 rounded-full border border-white/15 text-zinc-300">
          {formatTime(clip.start ?? 0)} → {formatTime(clip.end ?? 0)} ({duration}s)
        </span>
        <a
          href={`/api/clips/${clip.clip_id}/video`}
          download
          className="text-sm px-3 py-1.5 rounded-lg border border-white/20 text-zinc-200 hover:text-white hover:border-white/40 transition-colors"
        >
          Download
        </a>
        <Link
          to="/clips/$clipId/edit"
          params={{ clipId: clip.clip_id }}
          className="text-sm px-3 py-1.5 rounded-lg border border-white/20 text-zinc-200 hover:text-white hover:border-white/40 transition-colors"
        >
          Edit clip →
        </Link>
      </div>

      {/* Word-synced transcript */}
      {words.length > 0 && (
        <div className="bg-zinc-900 rounded-lg p-3 text-sm leading-relaxed">
          {words.map((w, i) => (
            <span
              key={i}
              onClick={() => { if (videoRef.current) videoRef.current.currentTime = w.start }}
              className="cursor-pointer rounded px-0.5 transition-colors"
              style={{
                color: activeWordIdx === i ? 'var(--score-green)' : undefined,
                fontWeight: activeWordIdx === i ? 700 : undefined,
              }}
            >
              {w.word}{' '}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function InfoPanel({ clip }: { clip: ClipRecord }) {
  return (
    <div className="space-y-5">
      <Link
        to="/jobs/$jobId"
        params={{ jobId: clip.job_id }}
        className="text-sm text-zinc-400 hover:text-zinc-100 underline"
      >
        ← Back to project
      </Link>

      {/* Score */}
      <div className="space-y-1">
        <p className="text-xs text-zinc-500 uppercase tracking-wide font-semibold">Virality Score</p>
        <ScoreBadge score={clip.virality_score} className="text-7xl font-bold" />
      </div>

      {/* Score breakdown */}
      {clip.score_breakdown && (
        <div className="space-y-2">
          {SCORE_KEYS.map((key) => (
            <div key={key} className="flex justify-between text-sm">
              <span className="text-zinc-400 capitalize">{key}</span>
              <span style={{ color: 'var(--score-green)', fontWeight: 600 }}>
                {scoreToGrade(clip.score_breakdown?.[key])}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Category badge */}
      {clip.score_breakdown?.category != null && (
        <span className="inline-block text-xs px-2.5 py-1 rounded-full border border-[#444] text-white">
          {String(clip.score_breakdown.category)}
        </span>
      )}

      <hr className="border-white/10" />

      {/* Summary */}
      {clip.summary && (
        <div>
          <p className="text-xs text-zinc-500 uppercase tracking-wide font-semibold mb-1">Summary</p>
          <p className="text-sm text-zinc-300">{clip.summary}</p>
        </div>
      )}

      {/* Title */}
      <div>
        <h1 className="text-xl font-bold text-white">{clip.title ?? 'Untitled clip'}</h1>
      </div>
    </div>
  )
}
