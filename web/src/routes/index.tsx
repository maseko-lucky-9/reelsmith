import { createRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import { ClipCard } from '@/components/dashboard/ClipCard'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: LibraryPage,
})

function LibraryPage() {
  const [search, setSearch] = useState('')
  const [minScore, setMinScore] = useState<number | undefined>()

  const clipsQuery = useQuery({
    queryKey: ['clips', search, minScore],
    queryFn: () => api.listClips({ search: search || undefined, min_score: minScore }),
  })

  const jobsQuery = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.listJobs(),
  })

  const clips = clipsQuery.data ?? []
  const jobs = jobsQuery.data ?? []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Library</h1>
        <p className="text-sm text-zinc-400">{jobs.length} job(s)</p>
      </div>

      <div className="flex gap-3">
        <Input
          placeholder="Search clips…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs bg-zinc-900 border-zinc-700"
        />
        <select
          className="rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100"
          value={minScore ?? ''}
          onChange={(e) =>
            setMinScore(e.target.value ? Number(e.target.value) : undefined)
          }
        >
          <option value="">All scores</option>
          <option value="67">High (67+)</option>
          <option value="34">Medium (34+)</option>
        </select>
      </div>

      {clipsQuery.isLoading && (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {Array.from({ length: 10 }).map((_, i) => (
            <Skeleton key={i} className="aspect-[9/16] rounded-lg bg-zinc-800" />
          ))}
        </div>
      )}

      {clipsQuery.isError && clips.length === 0 && (
        <p className="text-red-400 text-sm">Failed to load clips.</p>
      )}

      {!clipsQuery.isLoading && !clipsQuery.isError && clips.length === 0 && (
        <div className="text-center py-24">
          <p className="text-zinc-500 text-sm">No clips yet.</p>
          <a href="/jobs/new" className="text-zinc-400 hover:text-zinc-100 text-sm underline mt-2 block">
            Create your first job →
          </a>
        </div>
      )}

      {clips.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {clips.map((clip) => (
            <ClipCard key={clip.clip_id} clip={clip} />
          ))}
        </div>
      )}
    </div>
  )
}
