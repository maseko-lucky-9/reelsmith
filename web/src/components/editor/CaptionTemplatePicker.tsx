/**
 * Caption template gallery (W2.11).
 *
 * Five animated presets + a static fallback. Picker is presentational —
 * the parent owns selection state.
 */
export type CaptionStyleName =
  | 'static'
  | 'hormozi'
  | 'mrbeast'
  | 'karaoke'
  | 'boldpop'
  | 'subtle'

export interface CaptionTemplate {
  name: CaptionStyleName
  label: string
  description: string
  primary: string
  highlight?: string
}

export const CAPTION_TEMPLATES: CaptionTemplate[] = [
  {
    name: 'static',
    label: 'Static',
    description: 'No animation — clean and simple',
    primary: '#ffffff',
  },
  {
    name: 'hormozi',
    label: 'Hormozi',
    description: 'Bold yellow keyword highlights',
    primary: '#ffffff',
    highlight: '#fff200',
  },
  {
    name: 'mrbeast',
    label: 'MrBeast',
    description: 'Heavy outline, hot-pink emphasis',
    primary: '#ffffff',
    highlight: '#ff0066',
  },
  {
    name: 'karaoke',
    label: 'Karaoke',
    description: 'Word-by-word colour reveal',
    primary: '#ffffff',
    highlight: '#22d3ee',
  },
  {
    name: 'boldpop',
    label: 'Bold Pop',
    description: 'Pop-in animation with violet accent',
    primary: '#ffffff',
    highlight: '#7c3aed',
  },
  {
    name: 'subtle',
    label: 'Subtle',
    description: 'Light shadow, low contrast',
    primary: '#ffffff',
  },
]

interface Props {
  value: CaptionStyleName
  onChange: (name: CaptionStyleName) => void
}

export function CaptionTemplatePicker({ value, onChange }: Props) {
  return (
    <div
      data-testid="caption-template-picker"
      className="grid grid-cols-2 gap-2"
    >
      {CAPTION_TEMPLATES.map((tpl) => {
        const selected = value === tpl.name
        return (
          <button
            key={tpl.name}
            type="button"
            data-testid={`caption-template-${tpl.name}`}
            data-selected={selected}
            onClick={() => onChange(tpl.name)}
            className={`text-left rounded-md border px-3 py-2 transition-colors ${
              selected
                ? 'border-white/60 bg-white/5'
                : 'border-white/10 hover:border-white/30'
            }`}
          >
            <div className="text-xs font-medium text-zinc-100">{tpl.label}</div>
            <div className="text-[11px] text-zinc-500">{tpl.description}</div>
            {tpl.highlight ? (
              <div
                aria-hidden
                className="mt-1.5 h-1 rounded"
                style={{ background: tpl.highlight }}
              />
            ) : null}
          </button>
        )
      })}
    </div>
  )
}
