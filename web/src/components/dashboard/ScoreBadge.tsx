import { Badge } from '@/components/ui/badge'

interface ScoreBadgeProps {
  score: number | null | undefined
}

export function ScoreBadge({ score }: ScoreBadgeProps) {
  if (score == null) return <Badge variant="secondary">—</Badge>

  const variant =
    score >= 67 ? 'default' : score >= 34 ? 'secondary' : 'destructive'

  const colorClass =
    score >= 67
      ? 'bg-emerald-600 hover:bg-emerald-700 text-white'
      : score >= 34
        ? 'bg-amber-500 hover:bg-amber-600 text-white'
        : 'bg-red-600 hover:bg-red-700 text-white'

  return (
    <Badge variant={variant} className={colorClass}>
      {score}
    </Badge>
  )
}
