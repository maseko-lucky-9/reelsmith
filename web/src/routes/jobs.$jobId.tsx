import { createRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { rootRoute } from './root'
import { api } from '@/api/client'
import { useJobSSE } from '@/hooks/useJobSSE'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { ClipCard } from '@/components/dashboard/ClipCard'

export const jobDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/jobs/$jobId',
  component: JobDetailPage,
})

const STATUS_STEPS: Record<string, number> = {
  pending: 5,
  running: 50,
  completed: 100,
  failed: 100,
}

function JobDetailPage() {
  const { jobId } = jobDetailRoute.useParams()
  useJobSSE(jobId)

  const jobQuery = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.getJob(jobId),
    refetchInterval: (query) =>
      query.state.data?.status === 'running' ? 5000 : false,
  })

  const clipsQuery = useQuery({
    queryKey: ['clips', jobId],
    queryFn: () => api.listClips({ job_id: jobId }),
    enabled: jobQuery.data?.status === 'completed',
  })

  const job = jobQuery.data
  const clips = clipsQuery.data ?? []

  if (jobQuery.isLoading) {
    return (
      <div className="space-y-4 max-w-2xl">
        <Skeleton className="h-8 w-48 bg-zinc-800" />
        <Skeleton className="h-4 w-full bg-zinc-800" />
        <Skeleton className="h-4 w-3/4 bg-zinc-800" />
      </div>
    )
  }

  if (jobQuery.isError || !job) {
    return <p className="text-red-400">Job not found.</p>
  }

  const progress = STATUS_STEPS[job.status] ?? 50

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">{job.title ?? 'Processing…'}</h1>
          <p className="text-zinc-400 text-sm mt-1 break-all">{job.url}</p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {job.status !== 'completed' && job.status !== 'failed' && (
        <div className="space-y-1 max-w-md">
          <Progress value={progress} className="h-2" />
          <p className="text-xs text-zinc-500">{job.current_step ?? 'Waiting…'}</p>
        </div>
      )}

      {job.status === 'failed' && job.error && (
        <div className="rounded-md bg-red-950 border border-red-800 p-4 text-red-300 text-sm">
          {job.error}
        </div>
      )}

      {clips.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold mb-3">Clips ({clips.length})</h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
            {clips.map((c) => (
              <ClipCard key={c.clip_id} clip={c} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const cls =
    status === 'completed'
      ? 'bg-emerald-600 text-white'
      : status === 'failed'
        ? 'bg-red-600 text-white'
        : status === 'running'
          ? 'bg-blue-600 text-white'
          : 'bg-zinc-600 text-white'

  return <Badge className={cls}>{status}</Badge>
}
