import { createRoute } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Plus, X, ChevronDown } from 'lucide-react'
import { rootRoute } from './root'
import { api } from '@/api/client'
import { BrandTemplateCard } from '@/components/dashboard/BrandTemplateCard'
import { toast } from 'sonner'

export const brandTemplateRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/brand',
  component: BrandTemplatePage,
})

const ASPECT_RATIOS = [
  { label: '9:16', sublabel: 'TikTok / Reels', value: '9:16' },
  { label: '1:1', sublabel: 'LinkedIn / Facebook', value: '1:1' },
  { label: '16:9', sublabel: 'YouTube', value: '16:9' },
  { label: '4:5', sublabel: 'Instagram', value: '4:5' },
]

const AI_TOGGLES = [
  { key: 'remove_filler', label: 'Remove filler words', default: false },
  { key: 'remove_pauses', label: 'Remove pauses', default: false },
  { key: 'ai_keywords', label: 'AI keywords highlighter', default: true },
  { key: 'ai_emojis', label: 'AI emojis', default: true },
  { key: 'auto_broll', label: 'Auto generate stock B-Roll', default: false },
  { key: 'auto_transitions', label: 'Auto transitions', default: false },
]

function BrandTemplatePage() {
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showAspectModal, setShowAspectModal] = useState(false)
  const [captionFont, setCaptionFont] = useState('Geist Variable')
  const [captionColor, setCaptionColor] = useState('#ffffff')
  const [aiSettings, setAiSettings] = useState<Record<string, boolean>>(
    Object.fromEntries(AI_TOGGLES.map((t) => [t.key, t.default])),
  )

  const templatesQuery = useQuery({
    queryKey: ['brand-templates'],
    queryFn: () => api.listBrandTemplates(),
  })

  const createMutation = useMutation({
    mutationFn: (aspectRatio: string) =>
      api.createBrandTemplate({
        name: `New Template`,
        primary_color: captionColor,
        secondary_color: '#000000',
        caption_style: { font_family: captionFont, aspect_ratio: aspectRatio },
      }),
    onSuccess: (t) => {
      void queryClient.invalidateQueries({ queryKey: ['brand-templates'] })
      setSelectedId(t.id)
      setShowAspectModal(false)
      toast.success('Template created')
    },
    onError: () => toast.error('Failed to create template'),
  })

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!selectedId) throw new Error('No template selected')
      return api.updateBrandTemplate(selectedId, {
        primary_color: captionColor,
        caption_style: { font_family: captionFont, ...aiSettings },
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['brand-templates'] })
      toast.success('Template saved')
    },
    onError: () => toast.error('Failed to save template'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteBrandTemplate(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['brand-templates'] })
      setSelectedId(null)
      toast.success('Template deleted')
    },
    onError: () => toast.error('Failed to delete template'),
  })

  const templates = templatesQuery.data ?? []
  const selected = templates.find((t) => t.id === selectedId) ?? templates[0] ?? null

  return (
    <div className="flex flex-col h-full -mx-6 -mt-6 overflow-hidden" style={{ minHeight: 'calc(100vh - 80px)' }}>
      {/* Brand template top bar */}
      <div
        className="flex items-center gap-4 px-6 py-3 border-b border-white/8 flex-shrink-0"
        style={{ background: 'var(--sidebar-bg)' }}
      >
        <div>
          <h1 className="text-sm font-semibold text-white">Brand template</h1>
          <p className="text-xs text-zinc-500">Quickly setup your video template</p>
        </div>

        <div className="flex-1" />

        {/* Template selector */}
        <div className="relative">
          <select
            value={selectedId ?? ''}
            onChange={(e) => setSelectedId(e.target.value || null)}
            className="appearance-none pl-3 pr-8 py-1.5 rounded-lg border border-white/10 text-sm text-white bg-[var(--card-bg)] focus:outline-none"
          >
            <option value="">Select a template</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
          <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-400 pointer-events-none" />
        </div>

        <button
          onClick={() => setShowAspectModal(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-white/10 text-xs text-zinc-300 hover:text-white transition-colors"
          style={{ background: 'var(--card-bg)' }}
        >
          <Plus className="w-3.5 h-3.5" />
          New template
        </button>

        <button
          onClick={() => updateMutation.mutate()}
          disabled={!selected || updateMutation.isPending}
          className="px-3 py-1.5 rounded-lg bg-[var(--card-bg)] border border-white/20 text-sm text-white hover:border-white/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {updateMutation.isPending ? 'Saving…' : 'Save template'}
        </button>
      </div>

      {/* Two-column layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left settings panel */}
        <div
          className="w-64 flex-shrink-0 overflow-y-auto border-r border-white/8 p-4 space-y-6"
          style={{ background: 'var(--sidebar-bg)' }}
        >
          <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">Setting</p>

          {/* Style section */}
          <div className="space-y-3">
            <p className="text-xs font-semibold text-zinc-300">Style</p>

            <div className="space-y-2">
              <label className="text-xs text-zinc-400">Clip layout</label>
              <select className="w-full appearance-none pl-3 pr-8 py-1.5 rounded-lg border border-white/10 text-xs text-white bg-[var(--card-bg)] focus:outline-none">
                {ASPECT_RATIOS.map((r) => <option key={r.value}>{r.label} {r.sublabel}</option>)}
              </select>
            </div>

            <div className="space-y-2">
              <label className="text-xs text-zinc-400">Caption font</label>
              <input
                type="text"
                value={captionFont}
                onChange={(e) => setCaptionFont(e.target.value)}
                className="w-full px-3 py-1.5 rounded-lg border border-white/10 text-xs text-white bg-[var(--card-bg)] focus:outline-none focus:ring-1 focus:ring-white/20"
              />
            </div>

            <div className="space-y-2">
              <label className="text-xs text-zinc-400">Caption color</label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={captionColor}
                  onChange={(e) => setCaptionColor(e.target.value)}
                  className="w-8 h-8 rounded cursor-pointer border border-white/10"
                />
                <span className="text-xs text-zinc-400">{captionColor}</span>
              </div>
            </div>
          </div>

          {/* Brand section */}
          <div className="space-y-3">
            <p className="text-xs font-semibold text-zinc-300">Brand</p>
            {['Overlay (logo, CTA)', 'Intro/outro', 'Background music'].map((label) => (
              <button
                key={label}
                disabled
                className="w-full text-left text-xs px-3 py-2 rounded-lg border border-white/8 text-zinc-500 cursor-not-allowed opacity-60 flex items-center justify-between"
              >
                <span>{label}</span>
                <ChevronDown className="w-3 h-3" />
              </button>
            ))}
          </div>

          {/* AI section */}
          <div className="space-y-3">
            <p className="text-xs font-semibold text-zinc-300">AI</p>
            {AI_TOGGLES.map(({ key, label }) => (
              <div key={key} className="flex items-center justify-between">
                <span className="text-xs text-zinc-400">{label}</span>
                <button
                  onClick={() => setAiSettings((s) => ({ ...s, [key]: !s[key] }))}
                  className={`relative w-8 h-4 rounded-full transition-colors ${aiSettings[key] ? 'bg-white' : 'bg-zinc-600'}`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-black transition-transform ${aiSettings[key] ? 'translate-x-4' : 'translate-x-0'}`}
                  />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Center live preview */}
        <div className="flex-1 flex items-center justify-center bg-black relative">
          <div
            className="relative rounded-lg overflow-hidden flex items-end justify-center"
            style={{
              aspectRatio: '9/16',
              maxHeight: '70vh',
              background: '#111',
              minWidth: '200px',
            }}
          >
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-zinc-700 text-sm">Preview</span>
            </div>
            {/* Demo caption overlay */}
            <div className="absolute bottom-8 left-0 right-0 text-center px-4">
              <span
                className="text-lg font-bold drop-shadow-lg"
                style={{ fontFamily: captionFont, color: captionColor }}
              >
                SAMPLE CAPTION TEXT
              </span>
            </div>
            <span className="absolute top-3 right-3 text-[10px] px-2 py-0.5 rounded bg-black/50 text-zinc-300 border border-white/10">
              Demo
            </span>
          </div>
        </div>

        {/* Template list (right side) */}
        {templates.length > 0 && (
          <div className="w-64 flex-shrink-0 overflow-y-auto border-l border-white/8 p-4 space-y-3" style={{ background: 'var(--sidebar-bg)' }}>
            <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">All templates</p>
            {templates.map((t) => (
              <BrandTemplateCard
                key={t.id}
                template={t}
                onEdit={() => setSelectedId(t.id)}
                onDelete={() => deleteMutation.mutate(t.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Aspect ratio modal */}
      {showAspectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
          <div className="rounded-xl border border-white/10 p-6 space-y-5 w-80" style={{ background: '#1a1a1a' }}>
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-white">Choose aspect ratio</h2>
              <button onClick={() => setShowAspectModal(false)} className="text-zinc-400 hover:text-white transition-colors">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3">
              {ASPECT_RATIOS.map((r) => (
                <button
                  key={r.value}
                  onClick={() => createMutation.mutate(r.value)}
                  disabled={createMutation.isPending}
                  className="p-3 rounded-lg border border-white/10 hover:border-white/30 text-left transition-colors"
                  style={{ background: 'var(--card-bg)' }}
                >
                  <p className="text-sm font-semibold text-white">{r.label}</p>
                  <p className="text-xs text-zinc-500 mt-0.5">{r.sublabel}</p>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
