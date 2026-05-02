interface ScoreBadgeProps {
  score: number | null | undefined
  className?: string
}

export function ScoreBadge({ score, className }: ScoreBadgeProps) {
  if (score == null) return <span className={className ?? 'text-zinc-500'}>—</span>
  return (
    <span className={className} style={{ color: 'var(--score-green)', fontWeight: 700 }}>
      {score}
    </span>
  )
}
