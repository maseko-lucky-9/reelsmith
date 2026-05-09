import { createRoute, useNavigate, useSearch } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { Link2, Info, X, ChevronDown } from 'lucide-react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import type { PipelineOptions } from '@/api/client'
import { Skeleton } from '@/components/ui/skeleton'
import { AdvancedOptionsPanel } from '@/components/advanced-options-panel'
import { ALL_ON, ALL_OFF, SHORT_FORM_DEFAULTS, type ToggleId } from '@/lib/pipelineToggles'
import { isShortFormUrl } from '@/lib/detectPlatform'
import { toast } from 'sonner'

export const workflowRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/workflow',
  validateSearch: (search: Record<string, unknown>) => ({
    url: (search.url as string) ?? '',
  }),
  component: WorkflowPage,
})

function extractYouTubeId(url: string): string | null {
  const match = url.match(/(?:v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/)
  return match ? match[1] : null
}

function formatDuration(seconds: number): string {
  if (!seconds) return '0:00:00'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

type WorkflowMode = 'advanced' | 'auto' | 'chapter'

const TABS: { mode: WorkflowMode; label: string }[] = [
  { mode: 'advanced', label: 'Advanced' },
  { mode: 'auto', label: 'AI clipping' },
  { mode: 'chapter', label: "Don’t clip" },
]

export function WorkflowPage() {
  const navigate = useNavigate()
  const { url: encodedUrl } = useSearch({ from: '/workflow' })
  const decodedUrl = decodeURIComponent(encodedUrl)

  const [mode, setMode] = useState<WorkflowMode>('auto')
  const [prompt, setPrompt] = useState('')
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [copyrightChecked, setCopyrightChecked] = useState(false)

  // Advanced panel toggles — short-form sources default to Render 9:16 on, otherwise ALL OFF.
  const advancedDefaults = useMemo(
    () => (isShortFormUrl(decodedUrl) ? SHORT_FORM_DEFAULTS : ALL_OFF),
    [decodedUrl],
  )
  const [toggles, setToggles] = useState<Record<ToggleId, boolean>>({ ...advancedDefaults })

  const handleToggle = useCallback((id: ToggleId, value: boolean) => {
    setToggles((prev) => {
      const next = { ...prev, [id]: value }
      // Cascade: transcription off ⇒ captions off
      if (id === 'transcription' && !value) {
        next.captions = false
      }
      // Cascade: render off ⇒ reframe, broll, thumbnail off
      if (id === 'render' && !value) {
        next.reframe = false
        next.broll = false
        next.thumbnail = false
      }
      return next
    })
  }, [])

  // Reset toggles to platform-aware defaults when switching to advanced tab
  useEffect(() => {
    if (mode === 'advanced') {
      setToggles({ ...advancedDefaults })
    }
  }, [mode, advancedDefaults])

  const previewQuery = useQuery({
    queryKey: ['preview', decodedUrl],
    queryFn: () => api.previewVideo(decodedUrl),
    retry: false,
  })

  const templatesQuery = useQuery({
    queryKey: ['brand-templates'],
    queryFn: () => api.listBrandTemplates(),
  })

  const preview = previewQuery.data

  // Thumbnail: prefer proxy, fallback to YouTube regex
  const proxyThumbnailUrl = preview?.thumbnail
    ? `/api/jobs/preview/thumbnail?url=${encodeURIComponent(decodedUrl)}`
    : null
  const ytId = extractYouTubeId(decodedUrl)
  const ytFallbackUrl = ytId ? `https://img.youtube.com/vi/${ytId}/hqdefault.jpg` : null
  const thumbnailUrl = proxyThumbnailUrl ?? ytFallbackUrl

  function buildPipelineOptions(): PipelineOptions {
    if (mode === 'auto') {
      return { ...ALL_ON }
    }
    if (mode === 'chapter') {
      return { ...ALL_ON, segment_proposer: false }
    }
    // advanced mode: use current toggle state
    return { ...toggles }
  }

  const isDownloadOnly = mode === 'advanced' && !toggles.render
  const submitDisabled = submitting || (isDownloadOnly && !copyrightChecked)

  async function handleSubmit() {
    setSubmitting(true)
    try {
      const segmentMode = mode === 'chapter' ? 'chapter' : 'auto'
      const res = await api.createJob({
        url: decodedUrl,
        download_path: '/tmp/yt',
        segment_mode: segmentMode,
        prompt: prompt || undefined,
        auto_hook: false,
        brand_template_id: selectedTemplate ?? undefined,
        pipeline_options: buildPipelineOptions(),
      })
      void navigate({ to: '/jobs/$jobId', params: { jobId: res.job_id } })
    } catch {
      toast.error('Failed to create job. Please try again.')
      setSubmitting(false)
    }
  }

  // Tab keyboard navigation
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([])
  const handleTabKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
      let nextIndex: number | null = null
      if (e.key === 'ArrowRight') {
        nextIndex = (index + 1) % TABS.length
      } else if (e.key === 'ArrowLeft') {
        nextIndex = (index - 1 + TABS.length) % TABS.length
      }
      if (nextIndex !== null) {
        e.preventDefault()
        tabRefs.current[nextIndex]?.focus()
        setMode(TABS[nextIndex].mode)
      }
    },
    [],
  )

  return (
    <div className="max-w-2xl mx-auto space-y-8 pb-16 pt-4">
      {/* URL confirmation */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <div className="flex-1 relative">
            <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-400" />
            <input
              disabled
              value={decodedUrl}
              className="w-full pl-10 pr-3 py-2.5 rounded-xl text-sm text-zinc-400 border border-white/10 bg-[var(--card-bg)] truncate"
            />
          </div>
          <button
            onClick={() => void navigate({ to: '/' })}
            className="px-3 py-2 text-sm text-zinc-400 hover:text-white border border-white/10 rounded-xl transition-colors flex items-center gap-1.5"
          >
            <X className="w-3.5 h-3.5" />
            Remove
          </button>
        </div>

        <button
          onClick={() => void handleSubmit()}
          disabled={submitDisabled}
          className="w-full py-3 rounded-xl bg-white text-black font-semibold text-sm hover:bg-zinc-100 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {submitting ? 'Creating…' : 'Get clips in 1 click'}
        </button>

        {/* Options row */}
        <div className="flex items-center gap-4 text-xs text-zinc-400">
          <div className="flex items-center gap-1.5">
            <span>Speech language</span>
            <div className="flex items-center gap-1 border border-white/10 rounded-md px-2 py-1 bg-[var(--card-bg)]">
              <span className="text-white">English</span>
              <ChevronDown className="w-3 h-3" />
            </div>
          </div>
          <button className="underline hover:text-zinc-200 transition-colors" title="Coming soon">
            Upload .SRT (optional)
          </button>
          <div className="flex items-center gap-1">
            <span>⚡ 15</span>
            <Info className="w-3 h-3" />
          </div>
        </div>
      </div>

      {/* Video info */}
      <div className="space-y-3">
        {previewQuery.isLoading && (
          <Skeleton className="w-full aspect-video rounded-lg bg-zinc-800" />
        )}

        {thumbnailUrl && (
          <div className="relative w-full aspect-video rounded-lg overflow-hidden bg-zinc-900">
            <img src={thumbnailUrl} alt="Video thumbnail" className="w-full h-full object-cover" />
            {preview?.resolution && (
              <span className="absolute top-2 left-2 text-[10px] px-2 py-0.5 rounded bg-black/70 text-white font-medium">
                {preview.resolution}
              </span>
            )}
          </div>
        )}

        {preview?.title && (
          <p className="text-sm font-medium text-white">{preview.title}</p>
        )}

        <p className="text-[11px] text-zinc-500 leading-relaxed">
          Using video you don&apos;t own may violate copyright laws. By continuing, you confirm this is your own original content.
        </p>

        {/* Copyright checkbox gate for download-only mode */}
        {isDownloadOnly && (
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={copyrightChecked}
              onChange={(e) => setCopyrightChecked(e.target.checked)}
              className="accent-white w-4 h-4 mt-0.5 flex-shrink-0"
            />
            <span className="text-[11px] text-zinc-400 leading-relaxed">
              I confirm this is my own original content and I have the right to download it.
            </span>
          </label>
        )}
      </div>

      {/* AI settings — tabs */}
      <div className="space-y-4">
        <div
          className="flex gap-2 border-b border-white/8 pb-3"
          role="tablist"
          aria-label="Clipping mode"
        >
          {TABS.map((tab, i) => (
            <button
              key={tab.mode}
              ref={(el) => { tabRefs.current[i] = el }}
              role="tab"
              aria-selected={mode === tab.mode}
              aria-controls={`panel-${tab.mode}`}
              tabIndex={mode === tab.mode ? 0 : -1}
              onClick={() => setMode(tab.mode)}
              onKeyDown={(e) => handleTabKeyDown(e, i)}
              className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
                mode === tab.mode ? 'bg-white text-black' : 'text-zinc-400 hover:text-white'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Advanced panel */}
        {mode === 'advanced' && (
          <div role="tabpanel" id="panel-advanced" aria-labelledby="tab-advanced">
            <AdvancedOptionsPanel toggles={toggles} onToggle={handleToggle} />
          </div>
        )}

        {/* AI clipping panel */}
        {mode === 'auto' && (
          <div role="tabpanel" id="panel-auto" aria-labelledby="tab-auto">
            <div className="space-y-4">
              {/* Auto settings line replacing decorative dropdowns */}
              <p className="text-sm text-zinc-500">Auto settings — model, genre, length.</p>

              {/* Prompt input */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-sm text-zinc-300">Include specific moments</label>
                  <button className="text-xs text-zinc-500 underline hover:text-zinc-300 transition-colors">
                    Not sure how to prompt? learn more
                  </button>
                </div>
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="Example: find moments when we talked about the playoffs"
                  rows={2}
                  className="w-full rounded-xl bg-[var(--card-bg)] border border-white/10 px-3 py-2 text-sm text-white placeholder:text-zinc-500 resize-none focus:outline-none focus:ring-1 focus:ring-white/20"
                />
              </div>

              {/* Processing timeframe */}
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-zinc-300">Processing timeframe</span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full font-medium text-emerald-400 border border-emerald-400/40">
                    Credit saver
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <input
                    type="text"
                    readOnly
                    value="0:00:00"
                    className="w-20 text-center text-xs bg-[var(--card-bg)] border border-white/10 rounded-md px-2 py-1 text-zinc-300"
                  />
                  <div className="flex-1 h-1.5 bg-zinc-700 rounded-full">
                    <div className="h-full bg-white rounded-full w-full" />
                  </div>
                  <input
                    type="text"
                    readOnly
                    value={preview ? formatDuration(preview.duration) : '0:00:00'}
                    className="w-20 text-center text-xs bg-[var(--card-bg)] border border-white/10 rounded-md px-2 py-1 text-zinc-300"
                  />
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Don't clip panel */}
        {mode === 'chapter' && (
          <div role="tabpanel" id="panel-chapter" aria-labelledby="tab-chapter">
            <p className="text-sm text-zinc-500">
              Downloads and processes the full video without AI-based clipping. Uses native chapters if available.
            </p>
          </div>
        )}
      </div>

      {/* Brand template picker */}
      <div className="space-y-3">
        <div className="flex gap-2 border-b border-white/8 pb-3">
          <button className="px-4 py-1.5 text-sm font-medium text-white border-b-2 border-white">
            My templates
          </button>
          <button className="px-4 py-1.5 text-sm font-medium text-zinc-400 hover:text-white transition-colors">
            Quick presets
          </button>
        </div>

        {templatesQuery.isLoading && (
          <div className="grid grid-cols-2 gap-3">
            {[1, 2].map((i) => <Skeleton key={i} className="h-20 rounded-lg bg-zinc-800" />)}
          </div>
        )}

        {templatesQuery.data && templatesQuery.data.length === 0 && (
          <p className="text-sm text-zinc-500">No templates yet. Create one in Brand Template settings.</p>
        )}

        {templatesQuery.data && templatesQuery.data.length > 0 && (
          <div className="grid grid-cols-2 gap-3">
            {templatesQuery.data.map((t) => (
              <div
                key={t.id}
                onClick={() => setSelectedTemplate(t.id === selectedTemplate ? null : t.id)}
                className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                  selectedTemplate === t.id ? 'border-white bg-white/5' : 'border-white/10 hover:border-white/20'
                }`}
                style={{ background: selectedTemplate === t.id ? undefined : 'var(--card-bg)' }}
              >
                <p className="text-sm font-medium text-white">{t.name}</p>
                {t.caption_style && typeof t.caption_style === 'object' && (
                  <p className="text-xs text-zinc-500 mt-0.5">
                    {(t.caption_style as Record<string, string>).font_family ?? 'Default font'}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Save defaults */}
      <div className="flex justify-center">
        <button
          className="text-sm text-zinc-400 underline hover:text-zinc-200 transition-colors"
          title="Coming soon"
        >
          Save settings above as default
        </button>
      </div>
    </div>
  )
}
