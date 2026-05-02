import { useNavigate } from '@tanstack/react-router'
import { Video } from 'lucide-react'
import type { JobState } from '@/api/client'

interface ProjectCardProps {
  job: JobState
}

export function ProjectCard({ job }: ProjectCardProps) {
  const navigate = useNavigate()
  const clipCount = job.output_paths?.length ?? 0

  const statusColor =
    job.status === 'completed'
      ? 'bg-emerald-600 text-white'
      : job.status === 'running'
        ? 'bg-blue-600 text-white'
        : job.status === 'failed'
          ? 'bg-red-600 text-white'
          : 'bg-zinc-600 text-zinc-100'

  return (
    <div
      className="group cursor-pointer rounded-lg overflow-hidden ring-1 ring-white/8 hover:ring-white/20 transition-all"
      style={{ background: 'var(--card-bg)' }}
      onClick={() => void navigate({ to: '/jobs/$jobId', params: { jobId: job.job_id } })}
    >
      <div className="aspect-[16/9] bg-zinc-900 flex items-center justify-center relative overflow-hidden">
        <Video className="w-8 h-8 text-zinc-700" />
        <span className={`absolute top-2 left-2 text-[10px] px-1.5 py-0.5 rounded font-medium ${statusColor}`}>
          {job.status}
        </span>
      </div>
      <div className="p-3">
        <p className="text-sm text-white font-medium line-clamp-1">
          {job.title ?? job.url}
        </p>
        <p className="text-xs text-zinc-500 mt-0.5">{clipCount} clip{clipCount !== 1 ? 's' : ''}</p>
      </div>
    </div>
  )
}
