import type { ClipRecord } from '@/api/client'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScoreBadge } from './ScoreBadge'
import { Link } from '@tanstack/react-router'

interface ClipCardProps {
  clip: ClipRecord
}

export function ClipCard({ clip }: ClipCardProps) {
  const duration =
    clip.start != null && clip.end != null
      ? `${Math.round(clip.end - clip.start)}s`
      : null

  return (
    <Link to="/clips/$clipId" params={{ clipId: clip.clip_id }}>
      <Card className="overflow-hidden hover:ring-2 hover:ring-zinc-400 transition-all cursor-pointer">
        <div className="aspect-[9/16] bg-zinc-800 flex items-center justify-center relative">
          {clip.thumbnail_path ? (
            <img
              src={`/api/clips/${clip.clip_id}/thumbnail`}
              alt={clip.title ?? 'clip'}
              className="w-full h-full object-cover"
            />
          ) : (
            <span className="text-zinc-500 text-xs">No preview</span>
          )}
          <div className="absolute top-2 right-2">
            <ScoreBadge score={clip.virality_score} />
          </div>
        </div>
        <CardHeader className="p-3 pb-1">
          <CardTitle className="text-sm font-medium line-clamp-2">
            {clip.title ?? 'Untitled clip'}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-3 pt-0">
          <p className="text-xs text-zinc-400">{duration}</p>
        </CardContent>
      </Card>
    </Link>
  )
}
