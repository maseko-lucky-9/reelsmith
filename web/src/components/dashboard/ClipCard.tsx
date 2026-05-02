import { useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Play, ThumbsUp, ThumbsDown } from 'lucide-react'
import type { ClipRecord } from '@/api/client'
import { api } from '@/api/client'
import { formatTimestamp } from '@/lib/scoreToGrade'

interface ClipCardProps {
  clip: ClipRecord
  jobId: string
}

export function ClipCard({ clip, jobId }: ClipCardProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const likeMutation = useMutation({
    mutationFn: () => api.likeClip(clip.clip_id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['clips', jobId] }),
  })
  const dislikeMutation = useMutation({
    mutationFn: () => api.dislikeClip(clip.clip_id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['clips', jobId] }),
  })

  function handleCardClick(e: React.MouseEvent) {
    if ((e.target as HTMLElement).closest('button')) return
    void navigate({ to: '/clips/$clipId', params: { clipId: clip.clip_id } })
  }

  const startTs = formatTimestamp(clip.start ?? 0)
  const endTs = formatTimestamp(clip.end ?? 0)

  return (
    <div className="group cursor-pointer" onClick={handleCardClick}>
      {/* Thumbnail */}
      <div className="aspect-[9/16] overflow-hidden rounded-lg bg-zinc-900 relative">
        {clip.thumbnail_path ? (
          <img
            src={`/api/clips/${clip.clip_id}/thumbnail`}
            alt={clip.title ?? 'clip'}
            className="w-full h-full object-cover group-hover:brightness-110 group-hover:scale-[1.02] transition-all duration-150"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="text-zinc-600 text-xs">No preview</span>
          </div>
        )}

        {/* LOW-RES PREVIEW badge */}
        <span className="absolute top-2 left-2 text-[9px] px-1.5 py-0.5 rounded bg-black/60 text-zinc-300 font-medium select-none">
          LOW-RES PREVIEW
        </span>

        {/* Timestamp */}
        <span className="absolute top-2 right-2 text-[9px] px-1.5 py-0.5 rounded bg-black/60 text-white select-none">
          {startTs} → {endTs}
        </span>

        {/* Hover play overlay */}
        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-black/20">
          <Play className="w-8 h-8 text-white drop-shadow-lg" />
        </div>
      </div>

      {/* Below thumbnail */}
      <div className="mt-1.5 flex items-center justify-between px-0.5">
        <span className="text-2xl font-bold" style={{ color: 'var(--score-green)' }}>
          {clip.virality_score ?? '—'}
        </span>
        <div className="flex gap-0.5">
          <button
            onClick={(e) => { e.stopPropagation(); likeMutation.mutate() }}
            className={`p-1.5 rounded-md transition-colors ${clip.liked ? 'text-white bg-white/10' : 'text-zinc-500 hover:text-white hover:bg-white/8'}`}
            title="Like"
          >
            <ThumbsUp className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); dislikeMutation.mutate() }}
            className={`p-1.5 rounded-md transition-colors ${clip.disliked ? 'text-white bg-white/10' : 'text-zinc-500 hover:text-white hover:bg-white/8'}`}
            title="Dislike"
          >
            <ThumbsDown className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <p className="text-sm text-white mt-0.5 line-clamp-2 px-0.5">{clip.title ?? 'Untitled clip'}</p>

      {clip.score_breakdown?.category != null && (
        <span className="mt-1 inline-block text-xs px-2 py-0.5 rounded-full border border-[#444] text-white">
          {String(clip.score_breakdown.category)}
        </span>
      )}
    </div>
  )
}
