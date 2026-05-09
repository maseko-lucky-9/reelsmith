import { useMemo, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, Loader2, XCircle, Circle, Minus } from 'lucide-react'
import type { JobState } from '@/api/client'
import {
  deriveStageStates,
  describeActiveStage,
  describeSkippedStages,
  type DerivedStage,
  type PipelineEvent,
} from '@/lib/pipelineStages'

type Props = {
  job: JobState
}

/**
 * Live pipeline timeline rendered on /jobs/$jobId. Reads:
 *   - JobState (authoritative; React Query cache key ['job', jobId])
 *   - PipelineEvent[] (low-latency optimistic; cache key ['job-events', jobId])
 * Reconciles via deriveStageStates (max-merge). Pure render — no side effects.
 */
export function JobProgressTimeline({ job }: Props) {
  const eventsQuery = useQuery<PipelineEvent[]>({
    queryKey: ['job-events', job.job_id],
    // Events are populated by useJobSSE via setQueryData; this query never fetches.
    queryFn: async () => [],
    enabled: false,
    initialData: [],
  })

  const stages = useMemo(
    () => deriveStageStates(job, eventsQuery.data),
    [job, eventsQuery.data],
  )

  const announce = describeActiveStage(stages)

  // One-shot skipped-stages announcement on initial mount
  const skippedAnnouncedRef = useRef(false)
  const skippedAnnouncement = useMemo(() => describeSkippedStages(stages), [stages])
  useEffect(() => {
    if (skippedAnnouncement && !skippedAnnouncedRef.current) {
      skippedAnnouncedRef.current = true
    }
  }, [skippedAnnouncement])

  return (
    <div className="rounded-xl border border-white/10 bg-[var(--card-bg,#1a1a1a)] p-4">
      <ol role="list" className="space-y-1">
        {stages.map((s) => (
          <StageRow key={s.descriptor.id} stage={s} />
        ))}
      </ol>
      {/* Visually hidden live region — announces stage transitions + skipped stages. */}
      <div role="status" aria-live="polite" className="sr-only">
        {announce}
        {skippedAnnouncement && ` ${skippedAnnouncement}`}
      </div>
    </div>
  )
}

function StageRow({ stage }: { stage: DerivedStage }) {
  const { descriptor, state, done, total, error } = stage
  const isActive = state === 'active'
  const isDone = state === 'done'
  const isFailed = state === 'failed'
  const isSkipped = state === 'skipped'
  const isPerChapter = descriptor.perChapter && total !== null && total > 0

  const rowBg =
    isActive
      ? 'bg-zinc-800/60 motion-safe:animate-pulse'
      : isFailed
        ? 'bg-red-950/40'
        : ''

  const borderL = isActive
    ? 'border-l-2 border-l-emerald-400'
    : 'border-l-2 border-l-transparent'

  return (
    <li
      aria-current={isActive ? 'step' : undefined}
      className={`flex items-start gap-3 rounded-md py-2 pl-2 pr-3 transition-colors ${rowBg} ${borderL}`}
    >
      <span className="mt-0.5 flex-shrink-0">
        {isDone ? (
          <Check className="w-4 h-4 text-emerald-400" aria-label="done" />
        ) : isSkipped ? (
          <Minus className="w-4 h-4 text-zinc-500" aria-label="skipped" />
        ) : isFailed ? (
          <XCircle className="w-4 h-4 text-red-400" aria-label="failed" />
        ) : isActive ? (
          <Loader2
            className="w-4 h-4 text-emerald-300 motion-safe:animate-spin"
            aria-label="in progress"
          />
        ) : (
          <Circle className="w-4 h-4 text-zinc-600" aria-label="pending" />
        )}
      </span>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 text-sm">
          <span
            className={
              isDone
                ? 'text-zinc-300'
                : isSkipped
                  ? 'text-zinc-500'
                  : isActive
                    ? 'text-white font-medium'
                    : isFailed
                      ? 'text-red-300 font-medium'
                      : 'text-zinc-500'
            }
          >
            {descriptor.label}
          </span>
          {isPerChapter && !isSkipped && (
            <span className="text-xs text-zinc-500 tabular-nums">
              {done}/{total}
            </span>
          )}
        </div>

        {isSkipped && (
          <p className="mt-0.5 text-xs text-zinc-500">Skipped (per job options)</p>
        )}

        {isPerChapter && !isSkipped && (
          <div className="mt-1 h-1 w-full rounded-full bg-zinc-800 overflow-hidden">
            <div
              className={`h-full transition-all duration-300 ${
                isFailed ? 'bg-red-500' : 'bg-emerald-500'
              }`}
              style={{ width: `${total === 0 ? 0 : (done / total!) * 100}%` }}
            />
          </div>
        )}

        {isFailed && error && (
          <p className="mt-1 text-xs text-red-300/90">{error}</p>
        )}
      </div>
    </li>
  )
}
