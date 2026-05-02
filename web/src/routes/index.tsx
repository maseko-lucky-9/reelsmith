import { createRoute, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useRef, useState, useEffect } from 'react'
import {
  Link2,
  Scissors,
  Captions,
  Video,
  Mic2,
  Maximize,
  Film,
  Sparkles,
  FolderOpen,
} from 'lucide-react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import { ProjectCard } from '@/components/dashboard/ProjectCard'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

export const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: LibraryPage,
})

const PLACEHOLDERS = [
  'Drop a YouTube link…',
  'Drop a Zoom link…',
  'Drop a Vimeo link…',
]

const FEATURE_ICONS = [
  { label: 'Long to shorts', icon: Scissors, focusInput: true },
  { label: 'AI Captions', icon: Captions, focusInput: true },
  { label: 'Video editor', icon: Video, focusInput: false },
  { label: 'Enhance speech', icon: Mic2, focusInput: false },
  { label: 'AI Reframe', icon: Maximize, focusInput: false },
  { label: 'AI B-Roll', icon: Film, focusInput: false },
  { label: 'AI hook', icon: Sparkles, focusInput: false },
]

function LibraryPage() {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [url, setUrl] = useState('')
  const [placeholderIdx, setPlaceholderIdx] = useState(0)
  const [activeTab, setActiveTab] = useState<'all' | 'saved'>('all')

  const jobsQuery = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.listJobs(),
  })

  const jobs = jobsQuery.data ?? []
  const clipsTotal = jobs.reduce((acc, j) => acc + (j.output_paths?.length ?? 0), 0)

  useEffect(() => {
    const timer = setInterval(() => {
      setPlaceholderIdx((i) => (i + 1) % PLACEHOLDERS.length)
    }, 3000)
    return () => clearInterval(timer)
  }, [])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    void navigate({ to: '/workflow', search: { url: trimmed } })
  }

  return (
    <div className="relative space-y-16 pb-16">
      {/* Background watermark */}
      <span
        className="pointer-events-none select-none absolute top-0 left-1/2 -translate-x-1/2 font-bold text-white/5 whitespace-nowrap"
        style={{ fontSize: 'clamp(60px, 15vw, 180px)', zIndex: 0 }}
        aria-hidden
      >
        Reelsmith
      </span>

      {/* Hero section */}
      <section className="relative z-10 flex flex-col items-center gap-5 pt-16 max-w-2xl mx-auto">
        <form onSubmit={handleSubmit} className="w-full space-y-3">
          <div className="relative flex items-center">
            <Link2 className="absolute left-3.5 w-4 h-4 text-zinc-400 pointer-events-none" />
            <Input
              ref={inputRef}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder={PLACEHOLDERS[placeholderIdx]}
              className="pl-10 rounded-xl bg-[var(--card-bg)] border-white/10 text-white placeholder:text-zinc-500 h-12 text-sm"
            />
          </div>

          <div className="flex gap-2">
            <button
              type="button"
              disabled
              className="flex-1 px-4 py-2 rounded-xl border border-white/15 text-zinc-400 text-sm cursor-not-allowed opacity-60"
              title="Coming soon"
            >
              Upload
            </button>
            <button
              type="button"
              disabled
              className="flex-1 px-4 py-2 rounded-xl border border-white/15 text-zinc-400 text-sm cursor-not-allowed opacity-60"
              title="Coming soon"
            >
              Google Drive
            </button>
          </div>

          <button
            type="submit"
            className="w-full py-3 rounded-xl bg-white text-black font-semibold text-sm hover:bg-zinc-100 transition-colors"
          >
            Get clips in 1 click
          </button>
        </form>

        <p className="text-zinc-500 text-sm">
          or{' '}
          <button className="underline hover:text-zinc-300 transition-colors" onClick={() => inputRef.current?.focus()}>
            try a sample project
          </button>{' '}
          →
        </p>
      </section>

      {/* Feature icon row */}
      <section className="relative z-10 flex flex-wrap justify-center gap-6">
        {FEATURE_ICONS.map(({ label, icon: Icon, focusInput }) => (
          <button
            key={label}
            onClick={() => focusInput ? inputRef.current?.focus() : undefined}
            className={`flex flex-col items-center gap-2 ${!focusInput ? 'cursor-not-allowed opacity-60' : 'hover:opacity-90'}`}
            title={!focusInput ? 'Coming soon' : label}
          >
            <span
              className="w-16 h-16 rounded-full flex items-center justify-center"
              style={{ background: 'var(--icon-circle-bg)' }}
            >
              <Icon className="w-6 h-6 text-white" />
            </span>
            <span className="text-xs text-zinc-400 max-w-[72px] text-center leading-tight">{label}</span>
          </button>
        ))}
      </section>

      {/* Projects section */}
      <section className="relative z-10 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex gap-4 text-sm">
            <button
              onClick={() => setActiveTab('all')}
              className={`pb-1 border-b-2 transition-colors ${
                activeTab === 'all'
                  ? 'border-white text-white font-medium'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              All projects ({jobs.length})
            </button>
            <button
              onClick={() => setActiveTab('saved')}
              className={`pb-1 border-b-2 transition-colors ${
                activeTab === 'saved'
                  ? 'border-white text-white font-medium'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              Saved ({jobs.filter((j) => j.status === 'completed').length})
            </button>
          </div>
          <span className="text-xs text-zinc-500">{clipsTotal} clips created</span>
        </div>

        {jobsQuery.isLoading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="aspect-[16/9] rounded-lg bg-zinc-800" />
            ))}
          </div>
        )}

        {!jobsQuery.isLoading && jobs.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-20 text-center">
            <FolderOpen className="w-10 h-10 text-zinc-600" />
            <p className="text-zinc-400 text-sm">No projects yet.</p>
            <button
              className="text-zinc-300 text-sm underline hover:text-white transition-colors"
              onClick={() => inputRef.current?.focus()}
            >
              Paste a link above →
            </button>
          </div>
        )}

        {jobs.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
            {(activeTab === 'saved' ? jobs.filter((j) => j.status === 'completed') : jobs).map(
              (job) => <ProjectCard key={job.job_id} job={job} />,
            )}
          </div>
        )}
      </section>
    </div>
  )
}
