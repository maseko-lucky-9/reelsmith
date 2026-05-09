import { createRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { LayoutGrid, List, Search, SlidersHorizontal } from 'lucide-react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import { useJobSSE } from '@/hooks/useJobSSE'
import { Skeleton } from '@/components/ui/skeleton'
import { ClipCard } from '@/components/dashboard/ClipCard'
import { ClipListRow } from '@/components/dashboard/ClipListRow'
import { JobProgressTimeline } from '@/components/job-progress-timeline'
import { TimelineErrorBoundary } from '@/components/timeline-error-boundary'
import { PlatformChip } from '@/components/platform-chip'

export const jobDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/jobs/$jobId',
  component: JobDetailPage,
})

type ViewMode = 'list' | 'grid'
type FilterState = { liked: boolean; disliked: boolean; short: boolean }

function JobDetailPage() {
  const { jobId } = jobDetailRoute.useParams()
  useJobSSE(jobId)

  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try { return (localStorage.getItem('clips-view-mode') as ViewMode) ?? 'list' } catch { return 'list' }
  })
  const [search, setSearch] = useState('')
  const [filterOpen, setFilterOpen] = useState(false)
  const [filters, setFilters] = useState<FilterState>({ liked: false, disliked: false, short: false })

  useEffect(() => {
    try { localStorage.setItem('clips-view-mode', viewMode) } catch {}
  }, [viewMode])

  const jobQuery = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.getJob(jobId),
    refetchInterval: (query) => query.state.data?.status === 'running' ? 3000 : false,
  })

  const clipsQuery = useQuery({
    queryKey: ['clips', jobId],
    queryFn: () => api.listClips({ job_id: jobId }),
    refetchInterval: () => {
      const status = jobQuery.data?.status
      return status === 'running' ? 5000 : false
    },
  })

  const job = jobQuery.data
  const allClips = clipsQuery.data ?? []

  const filteredClips = allClips.filter((c) => {
    if (search) {
      const q = search.toLowerCase()
      const inTitle = (c.title ?? '').toLowerCase().includes(q)
      const inTranscript = JSON.stringify(c.transcript ?? {}).toLowerCase().includes(q)
      if (!inTitle && !inTranscript) return false
    }
    if (filters.liked && !c.liked) return false
    if (filters.disliked && !c.disliked) return false
    if (filters.short) {
      const dur = (c.end ?? 0) - (c.start ?? 0)
      if (dur < 60 || dur > 90) return false
    }
    return true
  })

  if (jobQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64 bg-zinc-800" />
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="aspect-[9/16] rounded-lg bg-zinc-800" />
          ))}
        </div>
      </div>
    )
  }

  if (jobQuery.isError || !job) {
    return <p className="text-red-400">Job not found.</p>
  }

  const showTimeline = job.status === 'running' || job.status === 'failed' || job.status === 'pending'

  return (
    <div className="space-y-4">
      {/* Header: title + platform chip + duration (visible during processing & after) */}
      {(job.source || job.title) && (
        <div className="flex items-center gap-2 flex-wrap">
          {job.source && <PlatformChip platform={job.source} />}
          <span className="text-sm font-medium text-white truncate max-w-md">
            {job.title ?? 'Loading title…'}
          </span>
          {job.duration != null && (
            <span className="text-xs text-zinc-500 tabular-nums">
              {formatDuration(job.duration)}
            </span>
          )}
        </div>
      )}

      {/* Live progress timeline during pending/running/failed */}
      {showTimeline && (
        <TimelineErrorBoundary>
          <JobProgressTimeline job={job} />
        </TimelineErrorBoundary>
      )}

      {/* Project top bar */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* View mode toggle */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => setViewMode('grid')}
            className={`p-2 rounded-md transition-colors ${viewMode === 'grid' ? 'bg-white/15 text-white' : 'text-zinc-400 hover:text-white hover:bg-white/8'}`}
            title="Grid view"
          >
            <LayoutGrid className="w-4 h-4" />
          </button>
          <button
            onClick={() => setViewMode('list')}
            className={`p-2 rounded-md transition-colors ${viewMode === 'list' ? 'bg-white/15 text-white' : 'text-zinc-400 hover:text-white hover:bg-white/8'}`}
            title="List view"
          >
            <List className="w-4 h-4" />
          </button>
        </div>

        {/* Job title */}
        <p className="text-sm font-medium text-white truncate max-w-[200px]">
          {job.title ?? 'Processing…'}
        </p>

        {/* Search */}
        <div className="flex-1 min-w-[200px] relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500 pointer-events-none" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Find keywords or moments… ⌘K"
            className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-[var(--card-bg)] border border-white/10 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:ring-1 focus:ring-white/20"
          />
        </div>
      </div>

      {/* Second row: filter + sort */}
      <div className="flex justify-end gap-2 relative">
        <button
          onClick={() => setFilterOpen(!filterOpen)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-xs text-zinc-300 hover:text-white hover:border-white/20 transition-colors"
          style={{ background: 'var(--card-bg)' }}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          Filter
        </button>
        <button
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-xs text-zinc-300 hover:text-white hover:border-white/20 transition-colors"
          style={{ background: 'var(--card-bg)' }}
          title="Coming soon"
          disabled
        >
          Sort
        </button>

        {filterOpen && (
          <div
            className="absolute top-9 right-0 z-20 p-4 rounded-xl border border-white/10 shadow-xl space-y-2.5 min-w-[180px]"
            style={{ background: '#1a1a1a' }}
          >
            {[
              { key: 'liked', label: 'Liked' },
              { key: 'disliked', label: 'Disliked' },
              { key: 'short', label: '60s–90s' },
            ].map(({ key, label }) => (
              <label key={key} className="flex items-center gap-2 cursor-pointer text-sm text-zinc-300 hover:text-white">
                <input
                  type="checkbox"
                  checked={filters[key as keyof FilterState]}
                  onChange={(e) => setFilters((f) => ({ ...f, [key]: e.target.checked }))}
                  className="accent-white w-3.5 h-3.5"
                />
                {label}
              </label>
            ))}
          </div>
        )}
      </div>

      {/* AI prompt text */}
      <p className="text-xs text-zinc-500">
        {job.prompt
          ? `${job.prompt} (${filteredClips.length})`
          : `Give me highlight compilations of all the exciting moments in this video (${filteredClips.length})`}
      </p>

      {/* Section header */}
      <p className="text-sm text-zinc-500">Original clips ({filteredClips.length})</p>

      {clipsQuery.isLoading && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="aspect-[9/16] rounded-lg bg-zinc-800" />
          ))}
        </div>
      )}

      {!clipsQuery.isLoading && filteredClips.length === 0 && allClips.length === 0 && job.status === 'completed' && (
        <p className="text-zinc-500 text-sm py-8 text-center">No clips generated.</p>
      )}

      {viewMode === 'grid' && filteredClips.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {filteredClips.map((c) => (
            <ClipCard key={c.clip_id} clip={c} jobId={jobId} />
          ))}
        </div>
      )}

      {viewMode === 'list' && filteredClips.length > 0 && (
        <div className="flex flex-col">
          {filteredClips.map((c, i) => (
            <ClipListRow key={c.clip_id} clip={c} rank={i + 1} jobId={jobId} />
          ))}
        </div>
      )}
    </div>
  )
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
