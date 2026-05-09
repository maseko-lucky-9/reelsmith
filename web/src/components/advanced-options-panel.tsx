import { TOGGLES, GROUP_LABELS, type ToggleId, type ToggleGroup } from '@/lib/pipelineToggles'

type Props = {
  toggles: Record<ToggleId, boolean>
  onToggle: (id: ToggleId, value: boolean) => void
}

const GROUP_ORDER: ToggleGroup[] = ['transcribe', 'render', 'segment']

export function AdvancedOptionsPanel({ toggles, onToggle }: Props) {
  return (
    <div className="space-y-4" data-testid="advanced-options-panel">
      {GROUP_ORDER.map((group) => {
        const groupToggles = TOGGLES.filter((t) => t.group === group)
        return (
          <div
            key={group}
            className="rounded-lg border border-white/10 bg-[var(--card-bg)] p-3 space-y-2"
          >
            <p className="text-xs font-medium text-zinc-400 uppercase tracking-wide">
              {GROUP_LABELS[group]}
            </p>
            {groupToggles.map((toggle) => {
              const isChild = toggle.parent != null
              const parentOff = toggle.parent ? !toggles[toggle.parent] : false
              const dependencyOff = toggle.dependsOn ? !toggles[toggle.dependsOn] : false
              const isDisabled = parentOff || dependencyOff
              const isOn = toggles[toggle.id]

              return (
                <div
                  key={toggle.id}
                  className={`flex items-center justify-between gap-3 py-1.5 ${
                    isChild ? 'ml-4' : ''
                  } ${isDisabled ? 'opacity-40' : ''}`}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white">{toggle.label}</p>
                    <p className="text-xs text-zinc-500 leading-snug">{toggle.helper}</p>
                  </div>
                  <button
                    role="switch"
                    aria-checked={isOn}
                    aria-label={toggle.label}
                    disabled={isDisabled}
                    onClick={() => onToggle(toggle.id, !isOn)}
                    className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
                      isOn ? 'bg-white' : 'bg-zinc-600'
                    } ${isDisabled ? 'cursor-not-allowed' : 'cursor-pointer'}`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-black transition-transform ${
                        isOn ? 'translate-x-4' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
              )
            })}
          </div>
        )
      })}
    </div>
  )
}
