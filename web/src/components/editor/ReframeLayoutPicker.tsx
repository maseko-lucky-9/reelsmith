/** Reframe aspect/layout picker (W2.11/12). */
export type AspectRatio = '9:16' | '1:1' | '16:9'
export type ReframeLayout = 'fullscreen' | 'split' | 'screenshare'

interface Props {
  aspect: AspectRatio
  layout: ReframeLayout
  onAspectChange: (next: AspectRatio) => void
  onLayoutChange: (next: ReframeLayout) => void
}

const ASPECTS: { value: AspectRatio; label: string }[] = [
  { value: '9:16', label: 'Reels / TikTok / Shorts' },
  { value: '1:1', label: 'Square — IG / LinkedIn' },
  { value: '16:9', label: 'YouTube' },
]

const LAYOUTS: { value: ReframeLayout; label: string }[] = [
  { value: 'fullscreen', label: 'Active speaker' },
  { value: 'split', label: 'Split-screen' },
  { value: 'screenshare', label: 'Screen-share' },
]

export function ReframeLayoutPicker({
  aspect,
  layout,
  onAspectChange,
  onLayoutChange,
}: Props) {
  return (
    <div data-testid="reframe-picker" className="space-y-3">
      <fieldset>
        <legend className="text-xs uppercase tracking-wide text-zinc-400">
          Aspect
        </legend>
        <div className="mt-1 flex gap-2">
          {ASPECTS.map((a) => (
            <button
              key={a.value}
              type="button"
              data-testid={`aspect-${a.value.replace(':', '-')}`}
              data-selected={a.value === aspect}
              onClick={() => onAspectChange(a.value)}
              className={`text-xs px-2 py-1 rounded border ${
                a.value === aspect
                  ? 'border-white/60 bg-white/5'
                  : 'border-white/10 hover:border-white/30'
              }`}
            >
              <span className="font-mono text-zinc-100">{a.value}</span>{' '}
              <span className="text-zinc-500">{a.label}</span>
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="text-xs uppercase tracking-wide text-zinc-400">
          Layout
        </legend>
        <div className="mt-1 flex gap-2">
          {LAYOUTS.map((l) => (
            <button
              key={l.value}
              type="button"
              data-testid={`layout-${l.value}`}
              data-selected={l.value === layout}
              onClick={() => onLayoutChange(l.value)}
              className={`text-xs px-2 py-1 rounded border ${
                l.value === layout
                  ? 'border-white/60 bg-white/5'
                  : 'border-white/10 hover:border-white/30'
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>
      </fieldset>
    </div>
  )
}
