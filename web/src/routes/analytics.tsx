/** /analytics — clip analytics dashboard (W3.11). */
import { createRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { rootRoute } from './root'
import { api } from '@/api/client'

export const analyticsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/analytics',
  component: AnalyticsPage,
})

function AnalyticsPage() {
  const clipsQuery = useQuery({
    queryKey: ['analytics-clips'],
    queryFn: () => api.listClips({}),
  })

  const clips = clipsQuery.data ?? []
  const total = clips.length
  const liked = clips.filter((c) => c.liked).length
  const avgScore =
    total === 0
      ? 0
      : Math.round(
          clips.reduce((acc, c) => acc + (c.virality_score ?? 0), 0) / total,
        )

  return (
    <div className="max-w-5xl mx-auto px-6 py-10 text-zinc-200">
      <h1 className="text-2xl font-semibold mb-6">Analytics</h1>

      <div className="grid grid-cols-3 gap-4 mb-8">
        <Stat label="Clips produced" value={total} />
        <Stat label="Liked clips" value={liked} />
        <Stat label="Avg virality score" value={avgScore} />
      </div>

      <p className="text-xs text-zinc-500">
        Per-platform Insights pulls require connected social accounts +
        the Wave 3 analytics pipeline running in the background. This
        view will show impressions / views / watch-time once snapshots
        accumulate.
      </p>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-white/10 p-5">
      <div className="text-xs uppercase tracking-wide text-zinc-400">
        {label}
      </div>
      <div className="mt-2 text-3xl font-semibold">{value}</div>
    </div>
  )
}
