import { Bell, Zap, Plus } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { api } from '@/api/client'

export function TopBar() {
  const navigate = useNavigate()

  const jobsQuery = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.listJobs(),
    refetchInterval: 5000,
  })

  const jobs = jobsQuery.data ?? []
  const runningCount = jobs.filter((j) => j.status === 'running').length
  const clipsCount = jobs.reduce((acc, j) => acc + (j.output_paths?.length ?? 0), 0)

  return (
    <header className="h-12 flex items-center justify-end px-4 gap-3 border-b border-white/8 flex-shrink-0" style={{ background: 'var(--sidebar-bg)' }}>
      {/* Notification bell */}
      <button className="relative p-1.5 text-zinc-400 hover:text-white transition-colors rounded-md hover:bg-white/10">
        <Bell className="w-4 h-4" />
        {runningCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-red-500 text-[9px] font-bold text-white flex items-center justify-center">
            {runningCount}
          </span>
        )}
      </button>

      {/* Credits / clips count */}
      <div className="flex items-center gap-1 text-sm text-zinc-300">
        <Zap className="w-3.5 h-3.5 text-yellow-400" />
        <span className="font-medium">{clipsCount}</span>
      </div>

      {/* New Clip button */}
      <button
        onClick={() => navigate({ to: '/' })}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-white text-black text-xs font-semibold hover:bg-zinc-200 transition-colors"
      >
        <Plus className="w-3 h-3" />
        New Clip
      </button>
    </header>
  )
}
