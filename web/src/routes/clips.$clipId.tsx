import { createRoute, Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useRef, useState, useEffect } from 'react'
import { rootRoute } from './root'
import { api, type ClipRecord } from '@/api/client'
import { ScoreBadge } from '@/components/dashboard/ScoreBadge'

export const clipDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/clips/$clipId',
  component: ClipDetailPage,
})

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
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 max-w-5xl">
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
        <div className="aspect-video bg-zinc-800 rounded-lg flex items-center justify-center">
          <p className="text-zinc-500 text-sm">Video not available</p>
        </div>
      )}

      <a
        href={`/api/clips/${clip.clip_id}/video`}
        download
        className="block text-center text-sm text-zinc-400 hover:text-zinc-100 underline"
      >
        Download clip
      </a>

      {words.length > 0 && (
        <div className="bg-zinc-900 rounded-lg p-3 text-sm leading-relaxed">
          {words.map((w, i) => (
            <span
              key={i}
              className={
                activeWordIdx === i
                  ? 'bg-yellow-400 text-black rounded px-0.5'
                  : 'text-zinc-300'
              }
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
  const duration =
    clip.start != null && clip.end != null
      ? `${clip.start.toFixed(1)}s – ${clip.end.toFixed(1)}s (${(clip.end - clip.start).toFixed(1)}s)`
      : null

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold">{clip.title ?? 'Untitled clip'}</h1>
        {duration && <p className="text-zinc-400 text-sm mt-1">{duration}</p>}
      </div>

      <div className="flex items-center gap-2">
        <span className="text-sm text-zinc-400">Virality score</span>
        <ScoreBadge score={clip.virality_score} />
      </div>

      {clip.score_breakdown && (
        <div className="space-y-1">
          <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">
            Score breakdown
          </p>
          <div className="bg-zinc-900 rounded-lg p-3 space-y-1">
            {Object.entries(clip.score_breakdown).map(([k, v]) => (
              <div key={k} className="flex justify-between text-sm">
                <span className="text-zinc-400 capitalize">{k}</span>
                <span className="font-mono">{Number(v).toFixed(2)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {clip.summary && (
        <div>
          <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1">
            Summary
          </p>
          <p className="text-sm text-zinc-300">{clip.summary}</p>
        </div>
      )}

      <div>
        <Link
          to="/jobs/$jobId"
          params={{ jobId: clip.job_id }}
          className="text-sm text-zinc-400 hover:text-zinc-100 underline"
        >
          ← Back to job
        </Link>
      </div>
    </div>
  )
}
