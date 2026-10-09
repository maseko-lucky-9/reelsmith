import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const NOT_APPLIED_LABEL = 'Not applied to renders yet'

/**
 * Marks a setting that is stored but not read by the render pipeline
 * (spec 001 FR-023: scaffolded-unwired).
 */
export function NotAppliedBadge({ className }: { className?: string }) {
  return (
    <Badge variant="outline" className={cn('text-amber-400 border-amber-400/40', className)}>
      {NOT_APPLIED_LABEL}
    </Badge>
  )
}
