import type { ClipRecord } from '@/api/client'

export interface SceneMatchInfo {
  emotion: string | null
  reason: string | null
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/**
 * What the scene-search judge said about a clip (spec 014): the dominant emotion and why
 * the clip matches the prompt. Stored in `score_breakdown` (a JSON column) as strings.
 */
export function sceneMatch(clip: Pick<ClipRecord, 'score_breakdown'>): SceneMatchInfo {
  const breakdown = (clip.score_breakdown ?? {}) as Record<string, unknown>
  return { emotion: text(breakdown.emotion), reason: text(breakdown.reason) }
}

/** Emotion chip and judge reason; renders nothing for a clip the judge did not describe. */
export function SceneMatch({ clip }: { clip: Pick<ClipRecord, 'score_breakdown'> }) {
  const { emotion, reason } = sceneMatch(clip)
  if (!emotion && !reason) return null
  return (
    <div className="mt-1 space-y-1">
      {emotion && (
        <span
          data-testid="emotion-chip"
          className="inline-block text-xs px-2 py-0.5 rounded-full border border-amber-400/40 text-amber-300 capitalize"
        >
          {emotion}
        </span>
      )}
      {reason && (
        <p className="text-xs text-zinc-400 line-clamp-2" title={reason}>
          {reason}
        </p>
      )}
    </div>
  )
}
