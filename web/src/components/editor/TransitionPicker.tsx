/** Transition picker (W2.6 frontend). */
export type TransitionKind = 'cut' | 'fade' | 'slide-left' | 'slide-right' | 'zoom'

const OPTIONS: { value: TransitionKind; label: string }[] = [
  { value: 'cut', label: 'Cut' },
  { value: 'fade', label: 'Fade' },
  { value: 'slide-left', label: 'Slide ←' },
  { value: 'slide-right', label: 'Slide →' },
  { value: 'zoom', label: 'Zoom' },
]

interface Props {
  value: TransitionKind
  onChange: (next: TransitionKind) => void
}

export function TransitionPicker({ value, onChange }: Props) {
  return (
    <div data-testid="transition-picker" className="flex gap-1 flex-wrap">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          data-testid={`transition-${o.value}`}
          data-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={`text-xs px-2 py-1 rounded border ${
            o.value === value
              ? 'border-white/60 bg-white/5'
              : 'border-white/10 hover:border-white/30'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
