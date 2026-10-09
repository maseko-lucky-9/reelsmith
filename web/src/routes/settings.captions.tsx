/** /settings/captions — caption template gallery (W2.11). */
import { createRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { rootRoute } from './root'
import { NotAppliedBadge } from '@/components/not-applied-badge'
import {
  CaptionTemplatePicker,
  type CaptionStyleName,
} from '@/components/editor/CaptionTemplatePicker'

export const captionsSettingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings/captions',
  component: CaptionsSettingsPage,
})

function CaptionsSettingsPage() {
  const [style, setStyle] = useState<CaptionStyleName>('hormozi')

  return (
    <div className="max-w-3xl mx-auto px-6 py-10 text-zinc-200">
      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-2xl font-semibold">Caption templates</h1>
        <NotAppliedBadge />
      </div>
      <p className="text-sm text-zinc-400 mb-4">
        Pick the default animation style applied to new clips. Existing clips
        retain their per-clip selection.
      </p>
      <CaptionTemplatePicker value={style} onChange={setStyle} />
      <p className="mt-4 text-xs text-zinc-500">
        Selected: <span className="font-mono">{style}</span>
      </p>
    </div>
  )
}
