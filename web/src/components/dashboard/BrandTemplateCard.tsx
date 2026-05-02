import { Pencil, Trash2 } from 'lucide-react'
import type { BrandTemplate } from '@/api/client'

interface BrandTemplateCardProps {
  template: BrandTemplate
  onEdit: () => void
  onDelete: () => void
}

export function BrandTemplateCard({ template, onEdit, onDelete }: BrandTemplateCardProps) {
  const fontName =
    template.caption_style && typeof template.caption_style === 'object'
      ? (template.caption_style as Record<string, string>).font_family ?? 'Default font'
      : 'Default font'

  return (
    <div
      className="group p-4 rounded-lg border border-white/10 hover:border-white/20 transition-colors space-y-3"
      style={{ background: 'var(--card-bg)' }}
    >
      {/* Color swatches */}
      <div className="flex gap-2">
        <div
          className="w-6 h-6 rounded-full border border-white/20"
          style={{ background: template.primary_color }}
          title={`Primary: ${template.primary_color}`}
        />
        <div
          className="w-6 h-6 rounded-full border border-white/20"
          style={{ background: template.secondary_color }}
          title={`Secondary: ${template.secondary_color}`}
        />
      </div>

      <div>
        <p className="text-sm font-medium text-white">{template.name}</p>
        <p className="text-xs text-zinc-500 mt-0.5">{fontName}</p>
      </div>

      <div className="flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={onEdit}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-white/15 text-zinc-300 hover:text-white transition-colors"
        >
          <Pencil className="w-3 h-3" />
          Edit
        </button>
        <button
          onClick={onDelete}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-red-800/50 text-red-400 hover:text-red-200 transition-colors"
        >
          <Trash2 className="w-3 h-3" />
          Delete
        </button>
      </div>
    </div>
  )
}
