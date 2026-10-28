import { useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Ban, Check, Trash2, Video } from 'lucide-react'
import { toast } from 'sonner'
import type { JobState, JobStatus } from '@/api/client'
import { api } from '@/api/client'
import { isActiveJob } from '@/lib/jobStatus'

const STATUS_COLORS: Record<JobStatus, string> = {
  completed: 'bg-emerald-600 text-white',
  running: 'bg-blue-600 text-white',
  failed: 'bg-red-600 text-white',
  cancelled: 'bg-amber-600 text-white',
  pending: 'bg-zinc-600 text-zinc-100',
}

interface ProjectCardProps {
  job: JobState
  /** Select mode (home page): a click toggles the card instead of opening the job. */
  selectable?: boolean
  selected?: boolean
  onToggleSelect?: (jobId: string) => void
}

export function ProjectCard({ job, selectable = false, selected = false, onToggleSelect }: ProjectCardProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const clipCount = job.output_paths?.length ?? 0
  const active = isActiveJob(job)
  const label = job.title ?? job.url

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['jobs'] })
  const cancelMutation = useMutation({
    mutationFn: () => api.cancelJob(job.job_id),
    onSuccess: () => toast.success('Job cancelled'),
    onError: (e: Error) => toast.error(`Could not cancel: ${e.message}`),
    onSettled: refresh,
  })
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteJob(job.job_id),
    onSuccess: () => toast.success('Job deleted'),
    onError: (e: Error) => toast.error(`Could not delete: ${e.message}`),
    onSettled: refresh,
  })

  function handleClick() {
    if (selectable) {
      if (!active) onToggleSelect?.(job.job_id)
      return
    }
    void navigate({ to: '/jobs/$jobId', params: { jobId: job.job_id } })
  }

  function handleCancel(e: React.MouseEvent) {
    e.stopPropagation()
    if (!window.confirm(`Cancel "${label}"? Its work so far is kept until you delete it.`)) return
    cancelMutation.mutate()
  }

  function handleDelete(e: React.MouseEvent) {
    e.stopPropagation()
    if (!window.confirm(`Delete "${label}" and all its clips and files? This cannot be undone.`)) return
    deleteMutation.mutate()
  }

  return (
    <div
      className={`group cursor-pointer rounded-lg overflow-hidden ring-1 transition-all ${
        selected ? 'ring-2 ring-white' : 'ring-white/8 hover:ring-white/20'
      } ${selectable && active ? 'opacity-60 cursor-not-allowed' : ''}`}
      style={{ background: 'var(--card-bg)' }}
      onClick={handleClick}
      data-testid="project-card"
    >
      <div className="aspect-[16/9] bg-zinc-900 flex items-center justify-center relative overflow-hidden">
        <Video className="w-8 h-8 text-zinc-700" />
        <span className={`absolute top-2 left-2 text-[10px] px-1.5 py-0.5 rounded font-medium ${STATUS_COLORS[job.status] ?? STATUS_COLORS.pending}`}>
          {job.status}
        </span>
        {selectable ? (
          <span
            role="checkbox"
            aria-checked={selected}
            aria-disabled={active}
            aria-label={`Select ${label}`}
            title={active ? 'Cancel this job before deleting it' : undefined}
            className={`absolute top-2 right-2 w-5 h-5 rounded border flex items-center justify-center ${
              selected ? 'bg-white border-white text-black' : 'border-white/50 bg-black/40'
            }`}
          >
            {selected && <Check className="w-3.5 h-3.5" />}
          </span>
        ) : active ? (
          <button
            type="button"
            onClick={handleCancel}
            disabled={cancelMutation.isPending}
            className="absolute top-2 right-2 flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/60 text-[11px] text-zinc-200 hover:text-white hover:bg-black/80 transition-colors"
            title="Cancel this job"
          >
            <Ban className="w-3 h-3" />
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
            aria-label={`Delete ${label}`}
            className="absolute top-2 right-2 p-1 rounded bg-black/60 text-zinc-300 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:text-red-400 hover:bg-black/80 transition-all"
            title="Delete this job"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      <div className="p-3">
        <p className="text-sm text-white font-medium line-clamp-1">
          {label}
        </p>
        <p className="text-xs text-zinc-500 mt-0.5">{clipCount} clip{clipCount !== 1 ? 's' : ''}</p>
      </div>
    </div>
  )
}
